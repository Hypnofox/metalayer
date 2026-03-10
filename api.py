"""
FastAPI REST API for Kubernetes Semantic Identity Resolver
Provides HTTP endpoints for flow correlation and pod identity lookups
"""
import os

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, IPvAnyAddress
from typing import Optional, Dict, List, Any
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response
import logging
import time
from datetime import datetime
from db import get_db_connector
from correlator import FlowCorrelator
from exporter import TopologyExporter

logger = logging.getLogger(__name__)

# Prometheus metrics
api_requests_total = Counter(
    'api_requests_total',
    'Total API requests',
    ['method', 'endpoint', 'status']
)
api_request_duration = Histogram(
    'api_request_duration_seconds',
    'API request duration',
    ['method', 'endpoint']
)
flow_correlations_total = Counter(
    'flow_correlations_total',
    'Total flow correlation requests',
    ['found_source', 'found_destination']
)


# Request/Response Models
class FlowCorrelationRequest(BaseModel):
    """Request model for flow correlation"""
    source_ip: str = Field(..., description="Source IP address")
    destination_ip: str = Field(..., description="Destination IP address")
    
    class Config:
        json_schema_extra = {
            "example": {
                "source_ip": "10.244.1.5",
                "destination_ip": "10.244.2.10"
            }
        }


class PodIdentity(BaseModel):
    """Pod identity information"""
    name: str
    namespace: str
    labels: Dict[str, str]
    uid: str
    node_name: Optional[str]
    service_account: Optional[str]
    phase: str


class FlowCorrelationResponse(BaseModel):
    """Response model for flow correlation"""
    source_ip: str
    destination_ip: str
    source_identity: Optional[PodIdentity]
    destination_identity: Optional[PodIdentity]
    timestamp: float = Field(default_factory=time.time)


class HealthResponse(BaseModel):
    """Health check response"""
    status: str


class ReadyResponse(BaseModel):
    """Readiness check response"""
    status: str
    k8s: Optional[str] = None


class PodListResponse(BaseModel):
    """Response for listing all pods"""
    total_pods: int
    pods: Dict[str, PodIdentity]


class TopologyExportRequest(BaseModel):
    """Request model for topology export"""
    start_time: Optional[str] = Field(None, description="Start time in ISO format")
    end_time: Optional[str] = Field(None, description="End time in ISO format")
    limit: Optional[int] = Field(None, description="Maximum number of flows to process")
    group_by_deployment: bool = Field(True, description="Group pods by deployment")
    
    class Config:
        json_schema_extra = {
            "example": {
                "start_time": "2025-12-14T00:00:00Z",
                "end_time": "2025-12-14T23:59:59Z",
                "limit": 10000,
                "group_by_deployment": True
            }
        }


class TopologyExportResponse(BaseModel):
    """Response model for topology export"""
    metadata: Dict[str, Any]
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]


def create_app(resolver) -> FastAPI:
    """Create and configure FastAPI application"""
    
    app = FastAPI(
        title="Kubernetes Semantic Identity Resolver API",
        description="REST API for correlating network flows with Kubernetes pod identities",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc"
    )
    
    # CORS middleware — set CORS_ORIGINS env var to a comma-separated list of allowed
    # origins (e.g. "https://app.example.com,https://admin.example.com").
    # Defaults to "*" when the variable is absent; restrict this in production.
    _cors_env = os.getenv("CORS_ORIGINS", "*")
    cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Store resolver instance
    app.state.resolver = resolver
    app.state.start_time = time.time()
    
    @app.middleware("http")
    async def metrics_middleware(request, call_next):
        """Middleware to track request metrics"""
        start_time = time.time()
        response = await call_next(request)
        duration = time.time() - start_time
        
        # Record metrics
        api_requests_total.labels(
            method=request.method,
            endpoint=request.url.path,
            status=response.status_code
        ).inc()
        
        api_request_duration.labels(
            method=request.method,
            endpoint=request.url.path
        ).observe(duration)
        
        return response
    
    @app.get("/health", response_model=HealthResponse, tags=["Health"])
    async def health_check():
        """Stateless health check endpoint"""
        # A simple response that does not depend on DB or K8s
        return HealthResponse(status="ok")
    
    @app.get("/ready", response_model=ReadyResponse, tags=["Health"])
    async def readiness_check():
        """Readiness check endpoint"""
        resolver = app.state.resolver
        # If connect_to_k8s failed at startup (e.g. no config), v1_api will be None.
        if resolver.v1_api is None:
            return ReadyResponse(status="ready", k8s="disconnected")
        
        # If it was connected, optionally ensure watcher is running.
        if resolver._watch_thread and resolver._watch_thread.is_alive():
            return ReadyResponse(status="ready", k8s="connected")
        elif resolver._watch_thread and not resolver._watch_thread.is_alive():
            # Connection was made but watch thread died, so we should fail readiness
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Kubernetes API is connected but watcher thread is not alive"
            )
        
        # Fallback if watcher hasn't initialized yet
        return ReadyResponse(status="ready", k8s="connected")
    
    @app.get("/metrics", tags=["Metrics"])
    async def metrics():
        """Prometheus metrics endpoint"""
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST
        )
    
    @app.post(
        "/api/v1/correlate",
        response_model=FlowCorrelationResponse,
        tags=["Flow Correlation"],
        status_code=status.HTTP_200_OK
    )
    async def correlate_flow(request: FlowCorrelationRequest):
        """
        Correlate a network flow with Kubernetes pod identities
        
        Given source and destination IP addresses, returns the corresponding
        pod identities if they exist in the lease table.
        """
        resolver = app.state.resolver
        
        try:
            result = resolver.correlate_flow(
                request.source_ip,
                request.destination_ip
            )
            
            # Track correlation metrics
            flow_correlations_total.labels(
                found_source=result['source_identity'] is not None,
                found_destination=result['destination_identity'] is not None
            ).inc()
            
            # Convert to response model
            response = FlowCorrelationResponse(
                source_ip=result['source_ip'],
                destination_ip=result['destination_ip'],
                source_identity=PodIdentity(**result['source_identity']) if result['source_identity'] else None,
                destination_identity=PodIdentity(**result['destination_identity']) if result['destination_identity'] else None
            )
            
            return response
            
        except Exception as e:
            logger.error(f"Error correlating flow: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to correlate flow: {str(e)}"
            )
    
    @app.get(
        "/api/v1/pods",
        response_model=PodListResponse,
        tags=["Pods"]
    )
    async def list_pods():
        """List all tracked pods and their identities"""
        resolver = app.state.resolver
        all_pods = resolver.lease_table.get_all_pods()
        
        # Convert to response format
        pods_dict = {
            ip: PodIdentity(**metadata)
            for ip, metadata in all_pods.items()
        }
        
        return PodListResponse(
            total_pods=len(pods_dict),
            pods=pods_dict
        )
    
    @app.get(
        "/api/v1/pods/{ip}",
        response_model=PodIdentity,
        tags=["Pods"]
    )
    async def get_pod_by_ip(ip: str):
        """Get pod identity by IP address"""
        resolver = app.state.resolver
        identity = resolver.lease_table.get_pod_identity(ip)
        
        if identity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No pod found with IP address: {ip}"
            )
        
        return PodIdentity(**identity)
    
    @app.post(
        "/api/v1/export-topology",
        response_model=TopologyExportResponse,
        tags=["Topology"],
        status_code=status.HTTP_200_OK
    )
    async def export_topology(request: TopologyExportRequest):
        """
        Export Kubernetes topology from network flows
        
        Queries network flows from the database, correlates them with pod identities,
        and generates a Faddom-compatible topology JSON with nodes and edges.
        """
        resolver = app.state.resolver
        
        try:
            # Parse time range
            start_time = None
            end_time = None
            
            if request.start_time:
                try:
                    start_time = datetime.fromisoformat(request.start_time.replace('Z', '+00:00'))
                except ValueError as e:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Invalid start_time format: {e}"
                    )
            
            if request.end_time:
                try:
                    end_time = datetime.fromisoformat(request.end_time.replace('Z', '+00:00'))
                except ValueError as e:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Invalid end_time format: {e}"
                    )
            
            # Query flows from database
            db_connector = get_db_connector()
            flows = db_connector.query_flows(
                start_time=start_time,
                end_time=end_time,
                limit=request.limit
            )
            
            logger.info(f"Retrieved {len(flows)} flows from database")

            # Map DB column names to correlator field names
            mapped_flows = [
                {
                    'source_ip': flow.get('source', ''),
                    'dest_ip': flow.get('target', ''),
                    'timestamp': flow.get('last_seen_at', datetime.now()),
                    'protocol': flow.get('protocol', 'TCP'),
                    'bytes': (flow.get('request_bytes') or 0) + (flow.get('response_bytes') or 0),
                }
                for flow in flows
            ]
            
            if mapped_flows:
                logger.debug(f"Sample normalized flow: {mapped_flows[0]}")

            # Correlate flows with pod identities
            correlator = FlowCorrelator(resolver)
            correlated_flows = correlator.correlate_flows(mapped_flows)
            
            logger.info(f"Correlated {len(correlated_flows)} flows")
            
            # Export topology
            exporter = TopologyExporter(group_by_deployment=request.group_by_deployment)
            topology = exporter.export_topology(
                correlated_flows=correlated_flows,
                start_time=start_time,
                end_time=end_time
            )
            
            return TopologyExportResponse(**topology)
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error exporting topology: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to export topology: {str(e)}"
            )
    
    return app
