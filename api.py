"""
FastAPI REST API for Kubernetes Semantic Identity Resolver
Provides HTTP endpoints for flow correlation and pod identity lookups
"""
from datetime import datetime
import logging
import time
from typing import Optional, Dict, List, Any

from fastapi import FastAPI, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel, Field, ConfigDict

from correlator import FlowCorrelator
from db import get_db_connector
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

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "source_ip": "10.244.1.5",
                "destination_ip": "10.244.2.10"
            }
        }
    )


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


class TopologyExportRequest(BaseModel):
    """Request model for topology export"""
    start_time: Optional[str] = Field(None, description="Start time in ISO format")
    end_time: Optional[str] = Field(None, description="End time in ISO format")
    limit: Optional[int] = Field(None, description="Maximum number of flows to process")
    group_by_deployment: bool = Field(True, description="Group pods by deployment")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "start_time": "2025-12-14T00:00:00Z",
                "end_time": "2025-12-14T23:59:59Z",
                "limit": 10000,
                "group_by_deployment": True
            }
        }
    )


class TopologyExportResponse(BaseModel):
    """Response model for topology export"""
    metadata: Dict[str, Any]
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]


class ResolveRequest(BaseModel):
    """Request model for single IP identity resolution."""
    ip: str = Field(..., description="IP address to resolve")


class ResolveBatchRequest(BaseModel):
    """Request model for batch identity resolution."""
    ips: List[str] = Field(..., description="List of IP addresses to resolve")


class ResolveResult(BaseModel):
    """Resolved identity payload for an IP."""
    ip: str
    identity: Optional[PodIdentity]


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

    # Store service state
    app.state.resolver = resolver
    app.state.start_time = time.time()
    app.state.flow_correlator = FlowCorrelator(resolver)
    app.state.topology_exporters = {}

    @app.middleware("http")
    async def metrics_middleware(request, call_next):
        """Middleware to track request metrics"""
        request_start = time.time()
        response = await call_next(request)
        duration = time.time() - request_start

        route = request.scope.get("route")
        endpoint = route.path if route and hasattr(route, 'path') else request.url.path

        # Record metrics
        api_requests_total.labels(
            method=request.method,
            endpoint=endpoint,
            status=str(response.status_code)
        ).inc()

        api_request_duration.labels(
            method=request.method,
            endpoint=endpoint
        ).observe(duration)

        return response

    @app.get("/health", response_model=HealthResponse, tags=["Health"])
    async def health_check():
        """Health check endpoint"""
        resolver_instance = app.state.resolver
        uptime = time.time() - app.state.start_time

        return HealthResponse(
            status="healthy",
            kubernetes_connected=resolver_instance.v1_api is not None,
            pods_tracked=len(resolver_instance.lease_table.get_all_pods()),
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
        resolver_instance = app.state.resolver

        try:
            result = resolver_instance.correlate_flow(
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
        resolver_instance = app.state.resolver
        all_pods = resolver_instance.lease_table.get_all_pods()

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
        resolver_instance = app.state.resolver
        identity = resolver_instance.lease_table.get_pod_identity(ip)

        if identity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No pod found with IP address: {ip}"
            )

        return PodIdentity(**identity)

    @app.post(
        "/api/v1/resolve",
        response_model=ResolveResult,
        tags=["Pods"],
        status_code=status.HTTP_200_OK
    )
    async def resolve_ip(request: ResolveRequest):
        """Resolve a single IP to Kubernetes identity."""
        resolver_instance = app.state.resolver
        identity = resolver_instance.lease_table.get_pod_identity(request.ip)
        return ResolveResult(
            ip=request.ip,
            identity=PodIdentity(**identity) if identity else None,
        )

    @app.post(
        "/api/v1/resolve/batch",
        response_model=List[ResolveResult],
        tags=["Pods"],
        status_code=status.HTTP_200_OK
    )
    async def resolve_ips(request: ResolveBatchRequest):
        """Resolve multiple IPs to Kubernetes identities."""
        resolver_instance = app.state.resolver
        results: List[ResolveResult] = []
        for ip in request.ips:
            identity = resolver_instance.lease_table.get_pod_identity(ip)
            results.append(
                ResolveResult(
                    ip=ip,
                    identity=PodIdentity(**identity) if identity else None,
                )
            )
        return results

    def _run_topology_export(start_time: Optional[datetime], end_time: Optional[datetime], limit: Optional[int], group_by_deployment: bool) -> Dict[str, Any]:
        db_connector = get_db_connector()
        flows = db_connector.query_flows(
            start_time=start_time,
            end_time=end_time,
            limit=limit
        )

        logger.info(f"Retrieved {len(flows)} flows from database")

        correlated_flows = app.state.flow_correlator.correlate_flows(flows)
        logger.info(f"Correlated {len(correlated_flows)} flows")

        exporters = app.state.topology_exporters
        exporter = exporters.get(group_by_deployment)
        if exporter is None:
            exporter = TopologyExporter(group_by_deployment=group_by_deployment)
            exporters[group_by_deployment] = exporter

        return exporter.export_topology(
            correlated_flows=correlated_flows,
            start_time=start_time,
            end_time=end_time
        )

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

            topology = await run_in_threadpool(
                _run_topology_export,
                start_time,
                end_time,
                request.limit,
                request.group_by_deployment,
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
