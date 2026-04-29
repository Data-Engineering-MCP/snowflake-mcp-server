from typing import Optional, Any, Dict, List


class CompareTables:
    def compare_tables(
        self,
        table1_name: str,
        table2_name: str,
        database_name: Optional[str] = None,
        schema_name: Optional[str] = None,
        columns_to_compare: Optional[List[str]] = None,
        where_clause: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compare two tables by row count, then optionally compare column data.

        Args:
            table1_name: First table name.
            table2_name: Second table name.xP
            database_name: Optional database qualifier.
            schema_name: Optional schema qualifier.
            columns_to_compare: Columns to select and compare if counts match.
            where_clause: SQL WHERE clause (without the WHERE keyword).
        """

        def fqn(table: str) -> str:
            parts = []
            if database_name:
                parts.append(database_name)
            if schema_name:
                parts.append(schema_name)
            parts.append(table)
            return ".".join(parts)

        # Build the WHERE clause and column list if provided by user
        where_fragment = f" WHERE {where_clause}" if where_clause else ""
        col_list = ", ".join(columns_to_compare) if columns_to_compare else "*"

        conn = self.verify_link()

        # Step 1: Count rows
        with conn.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {fqn(table1_name)}{where_fragment}")
            t1_count = cursor.fetchone()[0]

            cursor.execute(f"SELECT COUNT(*) FROM {fqn(table2_name)}{where_fragment}")
            t2_count = cursor.fetchone()[0]

        counts_match = t1_count == t2_count

        result: Dict[str, Any] = {
            "table1": fqn(table1_name),
            "table2": fqn(table2_name),
            "where_clause": where_clause,
            "row_counts": {
                "table1": t1_count,
                "table2": t2_count,
                "match": counts_match,
            },
        }

        # Step 2: If counts match, fetch column data
        if counts_match and columns_to_compare:
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT {col_list} FROM {fqn(table1_name)}{where_fragment}")
                col_names = [c[0] for c in cursor.description]
                t1_rows = [dict(zip(col_names, row)) for row in cursor.fetchall()]

                cursor.execute(f"SELECT {col_list} FROM {fqn(table2_name)}{where_fragment}")
                t2_rows = [dict(zip(col_names, row)) for row in cursor.fetchall()]

            result["data_comparison"] = {
                "columns": columns_to_compare,
                "table1_rows": t1_rows,
                "table2_rows": t2_rows,
            }

        # Step 3: Summary
        if not counts_match:
            summary = (
                f"Row counts differ: {fqn(table1_name)} has {t1_count} rows, "
                f"{fqn(table2_name)} has {t2_count} rows (difference: {abs(t1_count - t2_count)})."
            )
        elif not columns_to_compare:
            summary = f"Row counts match ({t1_count} rows). No columns specified for data comparison."
        else:
            summary = (
                f"Row counts match ({t1_count} rows). "
                f"Fetched columns {columns_to_compare} from both tables for comparison."
            )

        result["summary"] = summary
        return result
