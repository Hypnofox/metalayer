"""
Tests for topology exporter
"""
import pytest
from datetime import datetime
from exporter import TopologyExporter, TopologyNode, TopologyEdge
from correlator import CorrelatedFlow


def create_test_flow(source_ip, dest_ip, source_identity=None, dest_identity=None, bytes=1024):
    """Helper to create test flows"""
    return CorrelatedFlow(
        source_ip=source_ip,
        dest_ip=dest_ip,
        timestamp=datetime.now(),
        protocol='TCP',
        bytes=bytes,
        source_identity=source_identity,
        dest_identity=dest_identity
    )


def test_topology_node_creation():
    """Test creating topology nodes"""
    node = TopologyNode(
        node_id='pod:uid-123',
        node_type='pod',
        name='nginx-pod',
        namespace='default',
        labels={'app': 'nginx'}
    )
    
    node_dict = node.to_dict()
    assert node_dict['id'] == 'pod:uid-123'
    assert node_dict['type'] == 'pod'
    assert node_dict['name'] == 'nginx-pod'
    assert node_dict['namespace'] == 'default'
    assert node_dict['labels'] == {'app': 'nginx'}


def test_topology_edge_creation():
    """Test creating topology edges"""
    edge = TopologyEdge(
        source_id='pod:uid-1',
        dest_id='pod:uid-2',
        protocol='TCP'
    )
    
    edge.add_flow(1024)
    edge.add_flow(512)
    
    edge_dict = edge.to_dict()
    assert edge_dict['source'] == 'pod:uid-1'
    assert edge_dict['destination'] == 'pod:uid-2'
    assert edge_dict['protocol'] == 'TCP'
    assert edge_dict['bytes'] == 1536
    assert edge_dict['connections'] == 2


def test_export_pod_to_pod_topology():
    """Test exporting topology with pod-to-pod flows"""
    exporter = TopologyExporter(group_by_deployment=False)
    
    pod1 = {
        'name': 'nginx-pod',
        'namespace': 'default',
        'labels': {'app': 'nginx'},
        'uid': 'pod-uid-1',
        'node_name': 'node-1',
        'service_account': 'default',
        'phase': 'Running'
    }
    
    pod2 = {
        'name': 'redis-pod',
        'namespace': 'default',
        'labels': {'app': 'redis'},
        'uid': 'pod-uid-2',
        'node_name': 'node-2',
        'service_account': 'default',
        'phase': 'Running'
    }
    
    flows = [
        create_test_flow('10.244.1.5', '10.244.2.10', pod1, pod2, 1024),
        create_test_flow('10.244.1.5', '10.244.2.10', pod1, pod2, 512)
    ]
    
    topology = exporter.export_topology(flows)
    
    assert len(topology['nodes']) == 2
    assert len(topology['edges']) == 1
    assert topology['metadata']['total_flows'] == 2
    
    # Check edge aggregation
    edge = topology['edges'][0]
    assert edge['bytes'] == 1536
    assert edge['connections'] == 2


def test_export_with_external_services():
    """Test exporting topology with external services"""
    exporter = TopologyExporter(group_by_deployment=False)
    
    pod1 = {
        'name': 'nginx-pod',
        'namespace': 'default',
        'labels': {'app': 'nginx'},
        'uid': 'pod-uid-1',
        'node_name': 'node-1',
        'service_account': 'default',
        'phase': 'Running'
    }
    
    flows = [
        create_test_flow('10.244.1.5', '8.8.8.8', pod1, None, 1024)
    ]
    
    topology = exporter.export_topology(flows)
    
    assert len(topology['nodes']) == 2
    assert len(topology['edges']) == 1
    
    # Check for external node
    external_nodes = [n for n in topology['nodes'] if n['type'] == 'external']
    assert len(external_nodes) == 1
    assert external_nodes[0]['name'] == '8.8.8.8'


def test_export_grouped_by_deployment():
    """Test exporting topology grouped by deployment"""
    exporter = TopologyExporter(group_by_deployment=True)
    
    pod1 = {
        'name': 'nginx-deployment-abc-xyz',
        'namespace': 'default',
        'labels': {'app': 'nginx'},
        'uid': 'pod-uid-1',
        'node_name': 'node-1',
        'service_account': 'default',
        'phase': 'Running',
        'deployment': 'nginx-deployment'
    }
    
    pod2 = {
        'name': 'nginx-deployment-def-uvw',
        'namespace': 'default',
        'labels': {'app': 'nginx'},
        'uid': 'pod-uid-2',
        'node_name': 'node-2',
        'service_account': 'default',
        'phase': 'Running',
        'deployment': 'nginx-deployment'
    }
    
    pod3 = {
        'name': 'redis-deployment-ghi-rst',
        'namespace': 'default',
        'labels': {'app': 'redis'},
        'uid': 'pod-uid-3',
        'node_name': 'node-1',
        'service_account': 'default',
        'phase': 'Running',
        'deployment': 'redis-deployment'
    }
    
    flows = [
        create_test_flow('10.244.1.5', '10.244.2.10', pod1, pod3, 1024),
        create_test_flow('10.244.1.6', '10.244.2.10', pod2, pod3, 512)
    ]
    
    topology = exporter.export_topology(flows)
    
    # Should have 2 deployment nodes (nginx and redis), not 3 pod nodes
    assert len(topology['nodes']) == 2
    assert len(topology['edges']) == 1
    
    # Check node types
    deployment_nodes = [n for n in topology['nodes'] if n['type'] == 'deployment']
    assert len(deployment_nodes) == 2
    
    # Check edge aggregation across pods in same deployment
    edge = topology['edges'][0]
    assert edge['bytes'] == 1536
    assert edge['connections'] == 2


def test_export_with_time_range():
    """Test exporting topology with time range metadata"""
    exporter = TopologyExporter(group_by_deployment=False)
    
    pod1 = {
        'name': 'nginx-pod',
        'namespace': 'default',
        'labels': {'app': 'nginx'},
        'uid': 'pod-uid-1',
        'node_name': 'node-1',
        'service_account': 'default',
        'phase': 'Running'
    }
    
    flows = [
        create_test_flow('10.244.1.5', '8.8.8.8', pod1, None, 1024)
    ]
    
    start_time = datetime(2025, 12, 14, 0, 0, 0)
    end_time = datetime(2025, 12, 14, 23, 59, 59)
    
    topology = exporter.export_topology(flows, start_time, end_time)
    
    assert topology['metadata']['start_time'] == start_time.isoformat()
    assert topology['metadata']['end_time'] == end_time.isoformat()


def test_topology_summary():
    """Test topology summary statistics"""
    exporter = TopologyExporter(group_by_deployment=False)
    
    pod1 = {
        'name': 'nginx-pod',
        'namespace': 'default',
        'labels': {'app': 'nginx'},
        'uid': 'pod-uid-1',
        'node_name': 'node-1',
        'service_account': 'default',
        'phase': 'Running'
    }
    
    pod2 = {
        'name': 'redis-pod',
        'namespace': 'default',
        'labels': {'app': 'redis'},
        'uid': 'pod-uid-2',
        'node_name': 'node-2',
        'service_account': 'default',
        'phase': 'Running'
    }
    
    flows = [
        create_test_flow('10.244.1.5', '10.244.2.10', pod1, pod2, 1024),
        create_test_flow('10.244.1.5', '8.8.8.8', pod1, None, 512)
    ]
    
    topology = exporter.export_topology(flows)
    summary = exporter.get_summary(topology)
    
    assert summary['total_nodes'] == 3
    assert summary['total_edges'] == 2
    assert summary['total_bytes'] == 1536
    assert summary['total_connections'] == 2
    assert summary['node_types']['pod'] == 2
    assert summary['node_types']['external'] == 1


def test_skip_external_only_flows():
    """Test that flows with no pod endpoints are skipped"""
    exporter = TopologyExporter(group_by_deployment=False)
    
    flows = [
        create_test_flow('1.2.3.4', '8.8.8.8', None, None, 1024)
    ]
    
    topology = exporter.export_topology(flows)
    
    # Should have no nodes or edges since flow has no pod endpoints
    assert len(topology['nodes']) == 0
    assert len(topology['edges']) == 0
