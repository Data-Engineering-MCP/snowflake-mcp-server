from typing import Optional, Any, Dict, List


class DescribeTables:
    def describe_table(self, table_name: str, database_name: Optional[str] = None, schema_name: Optional[str] = None) -> Dict[str, Any]:
        """Get detailed information about a specific table."""
        # Build fully qualified table name
        parts = []
        if database_name:
            parts.append(database_name)
        if schema_name:
            parts.append(schema_name)
        parts.append(table_name)
        
        full_table_name = '.'.join(parts)
        
        # Get column information
        table_query = f"""
        DESCRIBE TABLE {full_table_name}
        """
            
        conn = self.verify_link()
        with conn.cursor() as cursor:
            cursor.execute(table_query)
            col_names = [col[0] for col in cursor.description]
            rows = cursor.fetchall()
            columns = [dict(zip(col_names, row)) for row in rows]

        return {
            "table_name": full_table_name,
            "columns": columns,
            "column_count": len(columns)
        }