# Kubernetes Configuration Directory

Place your kubeconfig file here as `kubeconfig` if you want to use a custom Kubernetes cluster configuration.

If no kubeconfig is provided here, the service will attempt to use:
1. In-cluster configuration (if running inside a Kubernetes pod)
2. Default kubeconfig from `~/.kube/config`

## Security Note

**Do not commit kubeconfig files to version control!** This directory should contain a `.gitignore` entry to prevent accidental commits of sensitive credentials.
