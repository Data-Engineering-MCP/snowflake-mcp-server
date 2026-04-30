#Future scope: This is a future scope tool to be used in the future to orchestrate the tools calls automatically.

from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
_Condition = Callable[[Dict, Dict], bool]
_ArgsBuilder = Callable[[Dict, Dict], Dict]
_Rule = Tuple[str, _Condition, str, _ArgsBuilder]


class Orchestrator:
    """
    Orchestration layer that chains MCP tool calls automatically.

    Two entry-points:
      • smart_execute(tool_name, arguments)
            Runs a single tool, then fires any matching auto-trigger rules
            based on the result content.  Returns the primary result together
            with every auto-triggered follow-up result.

      • run_pipeline(pipeline_name, arguments)
            Executes a named multi-step workflow where the output of each
            step feeds into the next.

    Both methods call the raw data methods on the connection class directly
    (e.g. self.compare_tables, self.describe_table …) so they work as a
    plain mixin inside SnowflakeConnection.
    """

    # ── AUTO-TRIGGER RULE REGISTRY ────────────────────────────────────────
    # Format per rule: (label, condition_fn, follow_up_method_name, args_builder_fn)
    # condition_fn(primary_result, original_arguments) -> bool
    # args_builder_fn(primary_result, original_arguments) -> kwargs dict

    def _auto_trigger_rules(self) -> Dict[str, List[_Rule]]:
        return {
            # ── compare_tables ──────────────────────────────────────────────
            # Always describe both tables after comparison to surface column
            # data-type information regardless of whether schemas match.
            "compare_tables": [
                (
                    "Post-compare → describe table1 (check column data types)",
                    lambda r, _a: True,
                    "describe_table",
                    lambda _r, a: {
                        "table_name": a["table1_name"],
                        "database_name": a.get("database_name"),
                        "schema_name": a.get("schema_name"),
                    },
                ),
                (
                    "Post-compare → describe table2 (check column data types)",
                    lambda r, _a: True,
                    "describe_table",
                    lambda _r, a: {
                        "table_name": a["table2_name"],
                        "database_name": a.get("database_name"),
                        "schema_name": a.get("schema_name"),
                    },
                ),
            ],

            # ── describe_table ──────────────────────────────────────────────
            "describe_table": [
                (
                    "Nullable columns detected → fetch column statistics",
                    lambda r, _a: any(
                        col.get("IS_NULLABLE") == "YES"
                        for col in r.get("columns", [])
                    ),
                    "_nullable_column_stats",
                    lambda r, a: {"_describe_result": r, "_parent_args": a},
                ),
            ],

            # ── list_tables ─────────────────────────────────────────────────
            "list_tables": [
                (
                    "Tables found → describe each (up to 3)",
                    lambda r, _a: isinstance(r, list) and len(r) > 0,
                    "_describe_listed_tables",
                    lambda r, a: {"_tables": r, "_parent_args": a},
                ),
            ],
        }

    # ── INTERNAL HELPER METHODS (used by auto-trigger rules) ──────────────

    def _nullable_column_stats(
        self,
        _describe_result: Dict[str, Any],
        _parent_args: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Return get_column_stats for every nullable column (capped at 5)."""
        nullable_cols = [
            col["COLUMN_NAME"]
            for col in _describe_result.get("columns", [])
            if col.get("IS_NULLABLE") == "YES"
        ][:5]

        return {
            col: self.get_column_stats(
                table_name=_parent_args["table_name"],
                column_name=col,
                database_name=_parent_args.get("database_name"),
                schema_name=_parent_args.get("schema_name"),
            )
            for col in nullable_cols
        }

    def _describe_listed_tables(
        self,
        _tables: List[Dict[str, Any]],
        _parent_args: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Describe the first N tables returned by list_tables."""
        max_tables = 3
        descriptions: Dict[str, Any] = {}
        for row in _tables[:max_tables]:
            # SHOW TABLES returns lowercase 'name' column
            tname = row.get("name") or row.get("NAME") or row.get("TABLE_NAME")
            if not tname:
                continue
            try:
                descriptions[tname] = self.describe_table(
                    table_name=tname,
                    database_name=_parent_args.get("database_name"),
                    schema_name=_parent_args.get("schema_name"),
                )
            except Exception as exc:
                descriptions[tname] = {"error": str(exc)}
        return {"table_descriptions": descriptions}

    # ── SMART EXECUTE ──────────────────────────────────────────────────────

    def smart_execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Execute *tool_name* with *arguments*, then automatically fire any
        follow-up tools whose trigger conditions are satisfied by the result.

        Returns:
            {
                "primary":       <result of the main tool>,
                "auto_triggered": [
                    {
                        "trigger_reason": <rule label>,
                        "tool":           <follow-up tool name>,
                        "arguments":      <args used>,
                        "result":         <follow-up result>  # or "error": <msg>
                    },
                    ...
                ]
            }
        """
        method = getattr(self, tool_name, None)
        if method is None:
            raise ValueError(f"Unknown tool: '{tool_name}'")

        primary_result = method(**arguments)
        triggered: List[Dict[str, Any]] = []

        for label, condition, follow_up_name, args_builder in self._auto_trigger_rules().get(tool_name, []):
            try:
                if not condition(primary_result, arguments):
                    continue
                follow_up_kwargs = args_builder(primary_result, arguments)
                follow_up_method = getattr(self, follow_up_name)
                follow_up_result = follow_up_method(**follow_up_kwargs)
                # Strip internal _-prefixed keys from the logged arguments
                public_args = {k: v for k, v in follow_up_kwargs.items() if not k.startswith("_")}
                triggered.append({
                    "trigger_reason": label,
                    "tool": follow_up_name,
                    "arguments": public_args,
                    "result": follow_up_result,
                })
            except Exception as exc:
                triggered.append({
                    "trigger_reason": label,
                    "tool": follow_up_name,
                    "error": str(exc),
                })

        return {"primary": primary_result, "auto_triggered": triggered}

    # ── NAMED PIPELINES ────────────────────────────────────────────────────

    AVAILABLE_PIPELINES = {
        "table_health_check": (
            
            "describe_table → nullable column stats → sample data. "
            "Required args: table_name. Optional: database_name, schema_name."
        ),
        "full_table_comparison": (
            "compare_tables → describe both tables if schema mismatch → "
            "sample both tables if row-count differs. "
            "Required args: table1_name, table2_name. Optional: database_name, schema_name, "
            "columns_to_compare, where_clause."
        ),
        "schema_explorer": (
            "list_tables → describe each table (up to max_tables, default 5). "
            "Optional args: database_name, schema_name, max_tables."
        ),
    }

    def run_pipeline(
        self,
        pipeline_name: str,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Execute a named multi-step pipeline.

        Available pipelines:
          • table_health_check    – describe_table → nullable col stats → sample
          • full_table_comparison – compare_tables → conditional describe + sample
          • schema_explorer       – list_tables → describe each table (up to 5)
        """
        pipelines: Dict[str, Callable] = {
            "table_health_check": self._pipeline_table_health_check,
            "full_table_comparison": self._pipeline_full_table_comparison,
            "schema_explorer": self._pipeline_schema_explorer,
        }
        fn = pipelines.get(pipeline_name)
        if fn is None:
            raise ValueError(
                f"Unknown pipeline '{pipeline_name}'. "
                f"Available: {list(pipelines.keys())}"
            )
        return fn(arguments)

    # ── PIPELINE IMPLEMENTATIONS ───────────────────────────────────────────

    def _pipeline_table_health_check(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Step 1 – describe_table
        Step 2 – get_column_stats for every nullable column (up to 5)
        Step 3 – get_table_sample
        """
        table_name: str = args["table_name"]
        database_name: Optional[str] = args.get("database_name")
        schema_name: Optional[str] = args.get("schema_name")

        steps: Dict[str, Any] = {}

        # 1. Describe
        steps["describe_table"] = self.describe_table(table_name, database_name, schema_name)

        # 2. Nullable column stats
        nullable_cols = [
            col["COLUMN_NAME"]
            for col in steps["describe_table"].get("columns", [])
            if col.get("IS_NULLABLE") == "YES"
        ][:5]
        steps["nullable_column_stats"] = {
            col: self.get_column_stats(table_name, col, database_name, schema_name)
            for col in nullable_cols
        }

        # 3. Sample data
        steps["table_sample"] = self.get_table_sample(
            table_name, database_name, schema_name, limit=10
        )

        return {"pipeline": "table_health_check", "steps": steps}

    def _pipeline_full_table_comparison(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Step 1 – compare_tables
        Step 2 – (conditional) describe both tables if schema mismatch
        Step 3 – (conditional) sample both tables if row counts differ
        """
        t1: str = args["table1_name"]
        t2: str = args["table2_name"]
        db: Optional[str] = args.get("database_name")
        sch: Optional[str] = args.get("schema_name")
        cols: Optional[List[str]] = args.get("columns_to_compare")
        where: Optional[str] = args.get("where_clause")

        steps: Dict[str, Any] = {}

        # 1. Compare
        steps["compare_tables"] = self.compare_tables(t1, t2, db, sch, cols, where)
        cmp = steps["compare_tables"]

        # 2. Schema mismatch → describe both
        if not cmp.get("schema_comparison", {}).get("schemas_match", True):
            steps["describe_table1"] = self.describe_table(t1, db, sch)
            steps["describe_table2"] = self.describe_table(t2, db, sch)

        # 3. Row-count difference → sample both
        if cmp.get("row_counts", {}).get("difference", 0) != 0:
            steps["sample_table1"] = self.get_table_sample(t1, db, sch, limit=5)
            steps["sample_table2"] = self.get_table_sample(t2, db, sch, limit=5)

        return {"pipeline": "full_table_comparison", "steps": steps}

    def _pipeline_schema_explorer(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Step 1 – list_tables in the given database/schema
        Step 2 – describe each table (up to max_tables)
        """
        database_name: Optional[str] = args.get("database_name")
        schema_name: Optional[str] = args.get("schema_name")
        max_tables: int = min(int(args.get("max_tables", 5)), 10)

        steps: Dict[str, Any] = {}

        # 1. List
        steps["list_tables"] = self.list_tables(database_name, schema_name)
        tables: List[Dict[str, Any]] = steps["list_tables"] if isinstance(steps["list_tables"], list) else []

        # 2. Describe each
        descriptions: Dict[str, Any] = {}
        for row in tables[:max_tables]:
            tname = row.get("name") or row.get("NAME") or row.get("TABLE_NAME")
            if not tname:
                continue
            try:
                descriptions[tname] = self.describe_table(tname, database_name, schema_name)
            except Exception as exc:
                descriptions[tname] = {"error": str(exc)}
        steps["table_descriptions"] = descriptions

        return {"pipeline": "schema_explorer", "steps": steps}
