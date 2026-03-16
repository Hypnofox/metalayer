"""
Topology Exporter
Generates Faddom-compatible topology JSON from correlated flows
"""
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime
from collections import defaultdict
from correlator import CorrelatedFlow

logger = logging.getLogger(__name__)


class TopologyNode:
    """Represents a node in the topology (pod, deployment, or external service)"""
    
    def __init__(
        self,
        node_id: str,
        node_type: str,
        name: str,
        namespace: Optional[str] = None,
        labels: Optional[Dict[str, str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.node_id = node_id
        self.node_type = node_type
        self.name = name
        self.namespace = namespace
        self.labels = labels or {}
        self.metadata = metadata or {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation"""
        node_dict = {
            'id': self.node_id,
            'type': self.node_type,
            'name': self.name,
        }
        
        if self.namespace:
            node_dict['namespace'] = self.namespace
        
        if self.labels:
            node_dict['labels'] = self.labels
        
        if self.metadata:
            node_dict['metadata'] = self.metadata
        
        return node_dict


class TopologyEdge:
    """Represents an edge in the topology (network flow between nodes)"""
    
    def __init__(
        self,
        source_id: str,
        dest_id: str,
        protocol: str = 'TCP',
        bytes_total: int = 0,
        connection_count: int = 0
    ):
        self.source_id = source_id
        self.dest_id = dest_id
        self.protocol = protocol
        self.bytes_total = bytes_total
        self.connection_count = connection_count
    
    def add_flow(self, bytes: int):
        """Add a flow to this edge"""
        self.bytes_total += bytes
        self.connection_count += 1
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation"""
        return {
            'source': self.source_id,
            'destination': self.dest_id,
            'protocol': self.protocol,
            'bytes': self.bytes_total,
            'connections': self.connection_count
        }


class TopologyExporter:
    """Exports topology data in Faddom-compatible format"""
    
    def __init__(self, group_by_deployment: bool = True):
        """
        Initialize the topology exporter
        
        Args:
            group_by_deployment: If True, group pods by deployment/app
        """
        self.group_by_deployment = group_by_deployment
        logger.info(f"Topology exporter initialized (group_by_deployment={group_by_deployment})")
    
    def export_topology(
        self,
        correlated_flows: List[CorrelatedFlow],
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Export topology from correlated flows
        
        Args:
            correlated_flows: List of correlated flows
            start_time: Start of time range
            end_time: End of time range
        
        Returns:
            Faddom-compatible topology JSON
        """
        nodes = {}
        edges = {}
        
        # Process flows to build nodes and edges
        for flow in correlated_flows:
            # Skip flows with no pod endpoints
            if not flow.has_pod_endpoint():
                continue
            
            # Create or get source node
            source_id = self._get_node_id(flow.source_ip, flow.source_identity)
            if source_id not in nodes:
                nodes[source_id] = self._create_node(flow.source_ip, flow.source_identity)
            
            # Create or get destination node
            dest_id = self._get_node_id(flow.dest_ip, flow.dest_identity)
            if dest_id not in nodes:
                nodes[dest_id] = self._create_node(flow.dest_ip, flow.dest_identity)
            
            # Create or update edge
            edge_key = f"{source_id}:{dest_id}:{flow.protocol}"
            if edge_key not in edges:
                edges[edge_key] = TopologyEdge(
                    source_id=source_id,
                    dest_id=dest_id,
                    protocol=flow.protocol
                )
            edges[edge_key].add_flow(flow.bytes)
        
        # Build topology JSON
        topology = {
            'metadata': {
                'generated_at': datetime.utcnow().isoformat(),
                'start_time': start_time.isoformat() if start_time else None,
                'end_time': end_time.isoformat() if end_time else None,
                'total_flows': len(correlated_flows),
                'total_nodes': len(nodes),
                'total_edges': len(edges)
            },
            'nodes': [node.to_dict() for node in nodes.values()],
            'edges': [edge.to_dict() for edge in edges.values()]
        }
        
        logger.info(
            f"Exported topology: {len(nodes)} nodes, {len(edges)} edges "
            f"from {len(correlated_flows)} flows"
        )
        
        return topology
    
    def _get_node_id(self, ip: str, identity: Optional[Dict[str, Any]]) -> str:
        """
        Get unique node ID
        
        Args:
            ip: IP address
            identity: Pod identity (if available)
        
        Returns:
            Unique node identifier
        """
        if identity is None:
            # External service - use IP as ID
            return f"external:{ip}"
        
        if self.group_by_deployment and 'deployment' in identity:
            # Group by deployment
            namespace = identity.get('namespace', 'default')
            deployment = identity['deployment']
            return f"deployment:{namespace}:{deployment}"
        
        # Use pod UID as unique identifier
        return f"pod:{identity['uid']}"
    
    def _create_node(self, ip: str, identity: Optional[Dict[str, Any]]) -> TopologyNode:
        """
        Create a topology node
        
        Args:
            ip: IP address
            identity: Pod identity (if available)
        
        Returns:
            TopologyNode instance
        """
        if identity is None:
            # External service node
            return TopologyNode(
                node_id=f"external:{ip}",
                node_type='external',
                name=ip,
                metadata={'ip': ip}
            )
        
        if self.group_by_deployment and 'deployment' in identity:
            # Deployment node
            namespace = identity.get('namespace', 'default')
            deployment = identity['deployment']
            
            return TopologyNode(
                node_id=f"deployment:{namespace}:{deployment}",
                node_type='deployment',
                name=deployment,
                namespace=namespace,
                labels=identity.get('labels', {}),
                metadata={
                    'app': identity.get('app'),
                    'app_name': identity.get('app_name'),
                    'component': identity.get('component')
                }
            )
        
        # Pod node
        return TopologyNode(
            node_id=f"pod:{identity['uid']}",
            node_type='pod',
            name=identity['name'],
            namespace=identity.get('namespace'),
            labels=identity.get('labels', {}),
            metadata={
                'uid': identity['uid'],
                'node_name': identity.get('node_name'),
                'service_account': identity.get('service_account'),
                'phase': identity.get('phase'),
                'ip': ip
            }
        )
    
    def get_summary(self, topology: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get summary statistics from topology
        
        Args:
            topology: Topology dictionary
        
        Returns:
            Summary statistics
        """
        nodes = topology.get('nodes', [])
        edges = topology.get('edges', [])
        
        # Count node types
        node_types = defaultdict(int)
        for node in nodes:
            node_types[node['type']] += 1
        
        # Calculate total bytes
        total_bytes = sum(edge.get('bytes', 0) for edge in edges)
        total_connections = sum(edge.get('connections', 0) for edge in edges)
        
        return {
            'total_nodes': len(nodes),
            'total_edges': len(edges),
            'node_types': dict(node_types),
            'total_bytes': total_bytes,
            'total_connections': total_connections
        }
