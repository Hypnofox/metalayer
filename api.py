"""
FastAPI REST API for Kubernetes Semantic Identity Resolver
Provides HTTP endpoints for flow correlation and pod identity lookups
"""
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, IPvAnyAddress
from typing import Optional, Dict, List
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response
import logging
import time

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
    kubernetes_connected: bool
    pods_tracked: int
    uptime_seconds: float


class PodListResponse(BaseModel):
    """Response for listing all pods"""
    total_pods: int
    pods: Dict[str, PodIdentity]


def create_app(resolver) -> FastAPI:
    """Create and configure FastAPI application"""
    
    app = FastAPI(
        title="Kubernetes Semantic Identity Resolver API",
        description="REST API for correlating network flows with Kubernetes pod identities",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc"
    )
    
    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately for production
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
        """Health check endpoint"""
        resolver = app.state.resolver
        uptime = time.time() - app.state.start_time
        
        return HealthResponse(
            status="healthy",
            kubernetes_connected=resolver.v1_api is not None,
            pods_tracked=len(resolver.lease_table.get_all_pods()),
            uptime_seconds=uptime
        )
    
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
    
    return app
