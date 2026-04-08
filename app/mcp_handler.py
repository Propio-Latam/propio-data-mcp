"""Dynamic MCP server factory — per-database and unified servers."""

import json
from mcp.server import Server
from mcp.types import Tool, TextContent

from app.db_registry import DatabaseConfig, list_databases, get_database_by_name
from app.db_pool import list_tables, describe_table, run_query, sample_data


def create_mcp_server(config: DatabaseConfig) -> Server:
    """Create an MCP Server instance wired to a specific PostgreSQL database."""

    server = Server(f"db-{config.name}")

    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        return [
            Tool(
                name="list_tables",
                description=f"List all tables in the '{config.name}' database ({config.description})",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="describe_table",
                description="Get the schema (columns, types) of a specific table",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "table": {"type": "string", "description": "Table name"},
                        "schema": {"type": "string", "description": "Schema name", "default": "public"},
                    },
                    "required": ["table"],
                },
            ),
            Tool(
                name="query",
                description="Execute a read-only SQL query against the database. Max 500 rows returned.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string", "description": "SQL SELECT query to execute"},
                    },
                    "required": ["sql"],
                },
            ),
            Tool(
                name="sample_data",
                description="Get sample rows from a table",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "table": {"type": "string", "description": "Table name"},
                        "schema": {"type": "string", "description": "Schema name", "default": "public"},
                        "limit": {"type": "integer", "description": "Number of rows", "default": 10},
                    },
                    "required": ["table"],
                },
            ),
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
        # Log the invocation (fire-and-forget, ignore errors)
        try:
            from app.portal.audit import log_query
            import asyncio
            asyncio.create_task(log_query(config.name, name, arguments))
        except Exception:
            pass

        try:
            if name == "list_tables":
                tables = await list_tables(config)
                return [TextContent(type="text", text=json.dumps(tables, indent=2, default=str))]

            elif name == "describe_table":
                cols = await describe_table(config, arguments["table"], arguments.get("schema", "public"))
                return [TextContent(type="text", text=json.dumps(cols, indent=2, default=str))]

            elif name == "query":
                sql = arguments["sql"].strip()
                first_word = sql.split()[0].upper() if sql else ""
                if first_word in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE", "GRANT", "REVOKE"):
                    return [TextContent(type="text", text="Error: only read-only queries are allowed")]
                rows = await run_query(config, sql)
                return [TextContent(type="text", text=json.dumps({"row_count": len(rows), "rows": rows}, indent=2, default=str))]

            elif name == "sample_data":
                rows = await sample_data(
                    config,
                    arguments["table"],
                    arguments.get("schema", "public"),
                    arguments.get("limit", 10),
                )
                return [TextContent(type="text", text=json.dumps({"row_count": len(rows), "rows": rows}, indent=2, default=str))]

            else:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]

        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    return server


def create_unified_mcp_server() -> Server:
    """Create a single MCP Server that can query ALL registered databases.

    Tools accept a 'database' parameter to specify which DB to query.
    New databases are discovered dynamically — no restart needed.
    """

    server = Server("propio-mcp")

    async def _resolve_db(name: str) -> DatabaseConfig:
        config = await get_database_by_name(name)
        if not config:
            raise ValueError(f"Database '{name}' not found. Use list_databases to see available databases.")
        return config

    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        return [
            Tool(
                name="list_databases",
                description="List all available databases with their names, descriptions, and table counts",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="list_tables",
                description="List all tables in a specific database",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "database": {"type": "string", "description": "Database name (from list_databases)"},
                    },
                    "required": ["database"],
                },
            ),
            Tool(
                name="describe_table",
                description="Get the schema (columns, types) of a table in a specific database",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "database": {"type": "string", "description": "Database name"},
                        "table": {"type": "string", "description": "Table name"},
                        "schema": {"type": "string", "description": "Schema name", "default": "public"},
                    },
                    "required": ["database", "table"],
                },
            ),
            Tool(
                name="query",
                description="Execute a read-only SQL query against a specific database. Max 500 rows.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "database": {"type": "string", "description": "Database name"},
                        "sql": {"type": "string", "description": "SQL SELECT query to execute"},
                    },
                    "required": ["database", "sql"],
                },
            ),
            Tool(
                name="sample_data",
                description="Get sample rows from a table in a specific database",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "database": {"type": "string", "description": "Database name"},
                        "table": {"type": "string", "description": "Table name"},
                        "schema": {"type": "string", "description": "Schema name", "default": "public"},
                        "limit": {"type": "integer", "description": "Number of rows", "default": 10},
                    },
                    "required": ["database", "table"],
                },
            ),
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            from app.portal.audit import log_query
            import asyncio
            db_name = arguments.get("database", "all")
            asyncio.create_task(log_query(db_name, name, arguments))
        except Exception:
            pass

        try:
            if name == "list_databases":
                dbs = await list_databases()
                result = []
                for db in dbs:
                    tables = await list_tables(db)
                    result.append({
                        "name": db.name,
                        "description": db.description,
                        "tables": len(tables),
                        "created_at": db.created_at,
                    })
                return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

            elif name == "list_tables":
                config = await _resolve_db(arguments["database"])
                tables = await list_tables(config)
                return [TextContent(type="text", text=json.dumps(tables, indent=2, default=str))]

            elif name == "describe_table":
                config = await _resolve_db(arguments["database"])
                cols = await describe_table(config, arguments["table"], arguments.get("schema", "public"))
                return [TextContent(type="text", text=json.dumps(cols, indent=2, default=str))]

            elif name == "query":
                config = await _resolve_db(arguments["database"])
                sql = arguments["sql"].strip()
                first_word = sql.split()[0].upper() if sql else ""
                if first_word in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE", "GRANT", "REVOKE"):
                    return [TextContent(type="text", text="Error: only read-only queries are allowed")]
                rows = await run_query(config, sql)
                return [TextContent(type="text", text=json.dumps({"row_count": len(rows), "rows": rows}, indent=2, default=str))]

            elif name == "sample_data":
                config = await _resolve_db(arguments["database"])
                rows = await sample_data(
                    config,
                    arguments["table"],
                    arguments.get("schema", "public"),
                    arguments.get("limit", 10),
                )
                return [TextContent(type="text", text=json.dumps({"row_count": len(rows), "rows": rows}, indent=2, default=str))]

            else:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]

        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    return server
