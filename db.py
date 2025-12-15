"""
PostgreSQL Database Connector for Faddom Network Flows
Provides connection management and flow query functionality
"""
import os
import logging
from typing import List, Dict, Optional, Any
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path
import psycopg2
from psycopg2 import pool, extras
from dotenv import load_dotenv

# Load environment variables from .env file in project root
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

logger = logging.getLogger(__name__)


class DatabaseConfig:
    """Database configuration from environment variables"""
    
    def __init__(self):
        self.host = os.getenv('DB_HOST', 'localhost')
        self.port = int(os.getenv('DB_PORT', '5432'))
        self.database = os.getenv('DB_NAME', 'faddom')
        self.user = os.getenv('DB_USER', 'faddom')
        self.password = os.getenv('DB_PASSWORD', '')
        self.min_connections = int(os.getenv('DB_MIN_CONN', '1'))
        self.max_connections = int(os.getenv('DB_MAX_CONN', '10'))
        self.flow_table = os.getenv('DB_FLOW_TABLE', 'tbl_network_topology')
    
    def get_connection_params(self) -> dict:
        """Get connection parameters as dictionary"""
        return {
            'host': self.host,
            'port': self.port,
            'database': self.database,
            'user': self.user,
            'password': self.password
        }


class DatabaseConnector:
    """PostgreSQL database connector with connection pooling"""
    
    def __init__(self, config: Optional[DatabaseConfig] = None):
        self.config = config or DatabaseConfig()
        self.connection_pool = None
        self._initialize_pool()
    
    def _initialize_pool(self):
        """Initialize the connection pool"""
        try:
            self.connection_pool = psycopg2.pool.ThreadedConnectionPool(
                self.config.min_connections,
                self.config.max_connections,
                **self.config.get_connection_params()
            )
            logger.info(
                f"Database connection pool initialized: "
                f"{self.config.host}:{self.config.port}/{self.config.database}"
            )
        except Exception as e:
            logger.error(f"Failed to initialize database connection pool: {e}")
            raise
    
    @contextmanager
    def get_connection(self):
        """Context manager for database connections"""
        conn = None
        try:
            conn = self.connection_pool.getconn()
            yield conn
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Database connection error: {e}")
            raise
        finally:
            if conn:
                self.connection_pool.putconn(conn)
    
    def query_flows(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Query network flows from the database
        
        Args:
            start_time: Start of time range (optional)
            end_time: End of time range (optional)
            limit: Maximum number of flows to return (optional)
        
        Returns:
            List of flow dictionaries with keys: 
            - source: source IP address
            - target: destination IP address
            - port: destination port
            - last_seen_at: timestamp when flow was last seen
            - protocol: IP protocol number
            - request_bytes: bytes sent from source
            - response_bytes: bytes sent from target
        """
        query = f"""
            SELECT 
                source,
                target,
                port,
                last_seen_at,
                protocol,
                request_bytes,
                response_bytes
            FROM {self.config.flow_table}
        """
        
        params = []
        conditions = []
        
        if start_time:
            conditions.append("last_seen_at >= %s")
            params.append(start_time)
        
        if end_time:
            conditions.append("last_seen_at <= %s")
            params.append(end_time)
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        query += " ORDER BY last_seen_at DESC"
        
        if limit:
            query += f" LIMIT {limit}"
        
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cursor:
                    cursor.execute(query, params)
                    flows = cursor.fetchall()
                    logger.info(f"Retrieved {len(flows)} flows from database")
                    return [dict(flow) for flow in flows]
        except Exception as e:
            logger.error(f"Error querying flows: {e}")
            raise
    
    def test_connection(self) -> bool:
        """Test database connectivity"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    result = cursor.fetchone()
                    logger.info("Database connection test successful")
                    return result[0] == 1
        except Exception as e:
            logger.error(f"Database connection test failed: {e}")
            return False
    
    def close(self):
        """Close all connections in the pool"""
        if self.connection_pool:
            self.connection_pool.closeall()
            logger.info("Database connection pool closed")


# Singleton instance
_db_connector: Optional[DatabaseConnector] = None


def get_db_connector() -> DatabaseConnector:
    """Get or create the database connector singleton"""
    global _db_connector
    if _db_connector is None:
        _db_connector = DatabaseConnector()
    return _db_connector


def close_db_connector():
    """Close the database connector singleton"""
    global _db_connector
    if _db_connector:
        _db_connector.close()
        _db_connector = None
