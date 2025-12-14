"""
Tests for flow correlation functionality
"""
import pytest
from datetime import datetime
from correlator import FlowCorrelator, CorrelatedFlow
from main import K8sIdentityResolver


class MockResolver:
    """Mock resolver for testing"""
    
    def __init__(self):
        self.lease_table = MockLeaseTable()


class MockLeaseTable:
    """Mock lease table for testing"""
    
    def __init__(self):
        self.pod_ip_map = {
            '10.244.1.5': {
                'name': 'nginx-deployment-abc123-xyz',
                'namespace': 'default',
                'labels': {'app': 'nginx'},
                'uid': 'pod-uid-1',
                'node_name': 'node-1',
                'service_account': 'default',
                'phase': 'Running'
            },
            '10.244.2.10': {
                'name': 'redis-deployment-def456-uvw',
                'namespace': 'default',
                'labels': {'app': 'redis'},
                'uid': 'pod-uid-2',
                'node_name': 'node-2',
                'service_account': 'default',
                'phase': 'Running'
            }
        }
    
    def get_pod_identity(self, ip: str):
        return self.pod_ip_map.get(ip)


def test_correlate_pod_to_pod():
    """Test correlating a flow between two pods"""
    resolver = MockResolver()
    correlator = FlowCorrelator(resolver)
    
    flow = correlator.correlate_flow(
        source_ip='10.244.1.5',
        dest_ip='10.244.2.10',
        timestamp=datetime.now(),
        protocol='TCP',
        bytes=1024
    )
    
    assert flow.source_identity is not None
    assert flow.dest_identity is not None
    assert flow.source_identity['name'] == 'nginx-deployment-abc123-xyz'
    assert flow.dest_identity['name'] == 'redis-deployment-def456-uvw'
    assert flow.is_pod_to_pod()


def test_correlate_pod_to_external():
    """Test correlating a flow from pod to external service"""
    resolver = MockResolver()
    correlator = FlowCorrelator(resolver)
    
    flow = correlator.correlate_flow(
        source_ip='10.244.1.5',
        dest_ip='8.8.8.8',
        timestamp=datetime.now(),
        protocol='TCP',
        bytes=512
    )
    
    assert flow.source_identity is not None
    assert flow.dest_identity is None
    assert flow.has_pod_endpoint()
    assert not flow.is_pod_to_pod()


def test_correlate_external_to_pod():
    """Test correlating a flow from external to pod"""
    resolver = MockResolver()
    correlator = FlowCorrelator(resolver)
    
    flow = correlator.correlate_flow(
        source_ip='1.2.3.4',
        dest_ip='10.244.2.10',
        timestamp=datetime.now(),
        protocol='TCP',
        bytes=2048
    )
    
    assert flow.source_identity is None
    assert flow.dest_identity is not None
    assert flow.has_pod_endpoint()
    assert not flow.is_pod_to_pod()


def test_correlate_external_to_external():
    """Test correlating a flow between external services"""
    resolver = MockResolver()
    correlator = FlowCorrelator(resolver)
    
    flow = correlator.correlate_flow(
        source_ip='1.2.3.4',
        dest_ip='8.8.8.8',
        timestamp=datetime.now(),
        protocol='TCP',
        bytes=256
    )
    
    assert flow.source_identity is None
    assert flow.dest_identity is None
    assert not flow.has_pod_endpoint()
    assert not flow.is_pod_to_pod()


def test_correlate_flows_batch():
    """Test batch correlation of multiple flows"""
    resolver = MockResolver()
    correlator = FlowCorrelator(resolver)
    
    flows = [
        {
            'source_ip': '10.244.1.5',
            'dest_ip': '10.244.2.10',
            'timestamp': datetime.now(),
            'protocol': 'TCP',
            'bytes': 1024
        },
        {
            'source_ip': '10.244.1.5',
            'dest_ip': '8.8.8.8',
            'timestamp': datetime.now(),
            'protocol': 'UDP',
            'bytes': 512
        }
    ]
    
    correlated = correlator.correlate_flows(flows)
    
    assert len(correlated) == 2
    assert correlated[0].is_pod_to_pod()
    assert not correlated[1].is_pod_to_pod()


def test_deployment_enrichment():
    """Test that pod identities are enriched with deployment information"""
    resolver = MockResolver()
    correlator = FlowCorrelator(resolver)
    
    flow = correlator.correlate_flow(
        source_ip='10.244.1.5',
        dest_ip='10.244.2.10',
        timestamp=datetime.now(),
        protocol='TCP',
        bytes=1024
    )
    
    # Check deployment name extraction
    assert 'deployment' in flow.source_identity
    assert flow.source_identity['deployment'] == 'nginx-deployment'
    assert 'deployment' in flow.dest_identity
    assert flow.dest_identity['deployment'] == 'redis-deployment'


def test_statistics():
    """Test correlation statistics"""
    resolver = MockResolver()
    correlator = FlowCorrelator(resolver)
    
    flows = [
        {
            'source_ip': '10.244.1.5',
            'dest_ip': '10.244.2.10',
            'timestamp': datetime.now(),
            'protocol': 'TCP',
            'bytes': 1024
        },
        {
            'source_ip': '10.244.1.5',
            'dest_ip': '8.8.8.8',
            'timestamp': datetime.now(),
            'protocol': 'TCP',
            'bytes': 512
        },
        {
            'source_ip': '1.2.3.4',
            'dest_ip': '5.6.7.8',
            'timestamp': datetime.now(),
            'protocol': 'TCP',
            'bytes': 256
        }
    ]
    
    correlated = correlator.correlate_flows(flows)
    stats = correlator.get_statistics(correlated)
    
    assert stats['total_flows'] == 3
    assert stats['pod_to_pod'] == 1
    assert stats['has_pod_endpoint'] == 2
    assert stats['external_only'] == 1
