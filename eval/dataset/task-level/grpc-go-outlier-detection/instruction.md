# Complete the xDS Outlier Detection Load Balancer

Our service-mesh data plane is built on grpc-go's xds client (the repo is at `/app`, which is the grpc-go source code). The control plane pushes `OutlierDetection` config in Cluster resources to passively eject endpoints that keep failing. The current problem: this feature is only half done — the xdsclient's parsing and validation of the OutlierDetection field in CDS, and the clusterresolver wiring that wraps the `cluster_impl` package inside the `outlier_detection_experimental` LB policy layer are all ready, but the `xds/internal/balancer/outlierdetection` balancer itself is still an empty shell: `Build` returns `nil` directly. As soon as the feature flag (`envconfig.XDSOutlierDetection`, currently hardcoded to `false` in the code; test scenarios may set it to `true` programmatically) is turned on, any xds client chain configured with outlier detection becomes unusable outright. Please implement this balancer completely.

The behavioral baseline is the design constraints already present in the repo: the gRFC A50 design proposal referenced in the `xds/internal/balancer/outlierdetection` package comment, plus the config structure and validation logic already written in `config.go` (keep and reuse them — don't overturn them and start over).

## External Behavior Contract

The following items are external semantics we've confirmed with the control plane and the business side; they must be strictly satisfied:

1. **A noop config must do no harm**: with the feature flag on, clusters for which the control plane has not pushed OutlierDetection (i.e. neither the `success_rate_ejection` nor the `failure_percentage_ejection` algorithm is configured) keep working as before, with RPCs completely normal.
2. **failure_percentage ejection**: the balancer periodically tallies RPC success/failure counts per address at the configured `interval`. When an address's failure rate over the previous period exceeds `threshold`, and its request volume for that period reaches `request_volume`, and the number of addresses satisfying the statistics conditions is no less than `minimum_hosts`, that address is ejected with probability `enforcement_percentage`. Ejected addresses no longer participate in picking, and traffic shifts to healthy addresses accordingly.
3. **success_rate ejection**: when an address's success rate falls below `均值 - 标准差 × (stdev_factor/1000)` (mean and stddev computed over all addresses meeting the `request_volume`/`minimum_hosts` thresholds), it is ejected with probability `enforcement_percentage`.
4. **Automatic recovery when ejection expires**: ejection is not permanent. Ejection duration is based on `base_ejection_time` and capped at `max_ejection_time`; after expiry the address automatically recovers and rejoins load balancing to share traffic. When the same address is ejected repeatedly, the ejection duration grows by a multiplier; for addresses that stay healthy after recovery, the multiplier gradually decays back.
5. **max_ejection_percent backstop**: at any moment the proportion of ejected addresses must not exceed `max_ejection_percent` — even if most addresses in the cluster are failing, they must not all be kicked out at once.

## Engineering Requirements

- Keep the existing package location, config structure, and `ParseConfig` validation semantics unchanged; the child policy in the LB config determines the real child load-balancing policy (e.g. `cluster_impl` all the way down to `round_robin`), and when the child policy type changes it must switch over smoothly; when the balancer is closed the child policy must also be closed correctly.
- The SubConn corresponding to an ejected address must appear unavailable to the child policy (report `TRANSIENT_FAILURE` and stop forwarding its real state updates); on recovery, hand the SubConn's latest real state back to the child policy. When a SubConn's address changes with resolver updates, the tracking relationship from addresses to ejection/statistics state must be maintained correctly.
- RPC counting happens in the pick-done callback and runs concurrently with the interval timer, config updates, and SubConn state updates — be careful to avoid data races.
- After the changes, `go build ./...` under `/app` must compile in full.

## Self-Test Suggestions

The repo has ready-made test infrastructure you can use directly: `internal/testutils/xds/e2e` can bring up a fake xDS management plane and push LDS/RDS/CDS/EDS resources, and `internal/stubserver` can run local backends with controllable behavior. We recommend building a multi-backend scenario to actually verify: a persistently failing backend gets ejected, traffic shifts to healthy backends, and it automatically recovers after ejection expiry. Confirm the behavior matches the contract above before wrapping up.
