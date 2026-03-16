"""
Flow Correlation Engine
Correlates network flows with Kubernetes pod identities
"""
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class CorrelatedFlow:
    """Represents a network flow correlated with pod identities"""
    
    def __init__(
        self,
        source_ip: str,
        dest_ip: str,
        timestamp: datetime,
        protocol: str,
        bytes: int,
        source_identity: Optional[Dict[str, Any]] = None,
        dest_identity: Optional[Dict[str, Any]] = None
    ):
        self.source_ip = source_ip
        self.dest_ip = dest_ip
        self.timestamp = timestamp
        self.protocol = protocol
        self.bytes = bytes
        self.source_identity = source_identity
        self.dest_identity = dest_identity
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation"""
        return {
            'source_ip': self.source_ip,
            'dest_ip': self.dest_ip,
            'timestamp': self.timestamp.isoformat() if isinstance(self.timestamp, datetime) else self.timestamp,
            'protocol': self.protocol,
            'bytes': self.bytes,
            'source_identity': self.source_identity,
            'dest_identity': self.dest_identity,
            'source_type': self._get_identity_type(self.source_identity),
            'dest_type': self._get_identity_type(self.dest_identity)
        }
    
    def _get_identity_type(self, identity: Optional[Dict]) -> str:
        """Determine the type of identity"""
        if identity is None:
            return 'external'
        return 'pod'
    
    def has_pod_endpoint(self) -> bool:
        """Check if at least one endpoint is a pod"""
        return self.source_identity is not None or self.dest_identity is not None
    
    def is_pod_to_pod(self) -> bool:
        """Check if both endpoints are pods"""
        return self.source_identity is not None and self.dest_identity is not None


class FlowCorrelator:
    """Correlates network flows with Kubernetes pod identities"""
    
    def __init__(self, resolver):
        """
        Initialize the flow correlator
        
        Args:
            resolver: K8sIdentityResolver instance with lease table
        """
        self.resolver = resolver
        logger.info("Flow correlator initialized")
    
    def correlate_flow(
        self,
        source_ip: str,
        dest_ip: str,
        timestamp: datetime,
        protocol: str = 'TCP',
        bytes: int = 0
    ) -> CorrelatedFlow:
        """
        Correlate a single flow with pod identities
        
        Args:
            source_ip: Source IP address
            dest_ip: Destination IP address
            timestamp: Flow timestamp
            protocol: Network protocol (TCP, UDP, etc.)
            bytes: Number of bytes transferred
        
        Returns:
            CorrelatedFlow object with pod identities if found
        """
        # Resolve source and destination IPs to pod identities
        source_identity = self.resolver.lease_table.get_pod_identity(source_ip)
        dest_identity = self.resolver.lease_table.get_pod_identity(dest_ip)
        
        # Enrich identities with deployment information
        if source_identity:
            source_identity = self._enrich_identity(source_identity)
        
        if dest_identity:
            dest_identity = self._enrich_identity(dest_identity)
        
        flow = CorrelatedFlow(
            source_ip=source_ip,
            dest_ip=dest_ip,
            timestamp=timestamp,
            protocol=protocol,
            bytes=bytes,
            source_identity=source_identity,
            dest_identity=dest_identity
        )
        
        logger.debug(
            f"Correlated flow {source_ip} -> {dest_ip}: "
            f"source={'pod' if source_identity else 'external'}, "
            f"dest={'pod' if dest_identity else 'external'}"
        )
        
        return flow
    
    def correlate_flows(self, flows: List[Dict[str, Any]]) -> List[CorrelatedFlow]:
        """
        Correlate multiple flows in batch
        
        Args:
            flows: List of flow dictionaries with keys: source_ip, dest_ip, timestamp, protocol, bytes
        
        Returns:
            List of CorrelatedFlow objects
        """
        correlated_flows = []
        
        for flow in flows:
            try:
                correlated = self.correlate_flow(
                    source_ip=flow['source'],
                    dest_ip=flow['target'],
                    timestamp=flow['last_seen_at'],
                    protocol=flow.get('protocol', 'TCP'),
                    bytes=flow.get('request_bytes', 0) + flow.get('response_bytes', 0)
                )
                correlated_flows.append(correlated)
            except Exception as e:
                logger.error(f"Error correlating flow {flow}: {e}")
                continue
        
        logger.info(
            f"Correlated {len(correlated_flows)} flows, "
            f"{sum(1 for f in correlated_flows if f.is_pod_to_pod())} pod-to-pod"
        )
        
        return correlated_flows
    
    def _enrich_identity(self, identity: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enrich pod identity with deployment/service information
        
        Args:
            identity: Pod identity dictionary
        
        Returns:
            Enriched identity with deployment/service metadata
        """
        enriched = identity.copy()
        labels = identity.get('labels', {})
        
        # Extract deployment information from labels
        if 'app' in labels:
            enriched['app'] = labels['app']
        
        if 'app.kubernetes.io/name' in labels:
            enriched['app_name'] = labels['app.kubernetes.io/name']
        
        if 'app.kubernetes.io/component' in labels:
            enriched['component'] = labels['app.kubernetes.io/component']
        
        # Extract deployment name from pod name (common pattern: deployment-name-xxxxx-yyyyy)
        pod_name = identity.get('name', '')
        if pod_name:
            # Try to extract deployment name (remove last two segments)
            parts = pod_name.rsplit('-', 2)
            if len(parts) > 2:
                enriched['deployment'] = parts[0]
            else:
                enriched['deployment'] = pod_name
        
        return enriched
    
    def get_statistics(self, correlated_flows: List[CorrelatedFlow]) -> Dict[str, Any]:
        """
        Get statistics about correlated flows
        
        Args:
            correlated_flows: List of correlated flows
        
        Returns:
            Dictionary with statistics
        """
        total = len(correlated_flows)
        pod_to_pod = 0
        has_pod = 0
        for f in correlated_flows:
            if f.is_pod_to_pod():
                pod_to_pod += 1
                has_pod += 1
            elif f.has_pod_endpoint():
                has_pod += 1
        external_only = total - has_pod
        
        return {
            'total_flows': total,
            'pod_to_pod': pod_to_pod,
            'has_pod_endpoint': has_pod,
            'external_only': external_only,
            'correlation_rate': (has_pod / total * 100) if total > 0 else 0
        }
