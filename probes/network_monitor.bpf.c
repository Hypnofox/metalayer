#include <uapi/linux/ptrace.h>
#include <net/sock.h>
#include <bcc/proto.h>

struct flow_event_t {
    u64 ts_ns;
    u32 saddr;
    u32 daddr;
    u16 sport;
    u16 dport;
};

BPF_PERF_OUTPUT(flow_events);

int trace_tcp_v4_connect(struct pt_regs *ctx, struct sock *sk) {
    struct flow_event_t event = {};

    u16 dport = 0;
    u16 sport = 0;
    u32 saddr = 0;
    u32 daddr = 0;

    bpf_probe_read_kernel(&dport, sizeof(dport), &sk->__sk_common.skc_dport);
    bpf_probe_read_kernel(&sport, sizeof(sport), &sk->__sk_common.skc_num);
    bpf_probe_read_kernel(&saddr, sizeof(saddr), &sk->__sk_common.skc_rcv_saddr);
    bpf_probe_read_kernel(&daddr, sizeof(daddr), &sk->__sk_common.skc_daddr);

    event.ts_ns = bpf_ktime_get_ns();
    event.saddr = saddr;
    event.daddr = daddr;
    event.sport = sport;
    event.dport = ntohs(dport);

    flow_events.perf_submit(ctx, &event, sizeof(event));
    return 0;
}
