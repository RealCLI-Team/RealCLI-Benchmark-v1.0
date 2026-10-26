# Behavior tests for the grpc-go xDS Outlier Detection balancer.
#
# These tests are black-box: they drop a Go contract-test harness into the
# repo at /app/test/odcontract and run it with `go test`. The harness drives
# the public xds data plane end to end - a fake xDS management server pushes
# LDS/RDS/CDS/EDS resources (with OutlierDetection settings on the Cluster),
# real local backends serve or fail RPCs, and the resulting traffic
# distribution is asserted: ejection of failing/outlier backends, automatic
# recovery after the ejection time, and the max_ejection_percent cap.
#
# On the pre-change snapshot the outlier detection balancer's Build() is a
# stub returning nil, so every scenario fails (the xds LB tree collapses once
# the feature is enabled). A correct implementation passes all of them.

import json
import os
import subprocess

import pytest

APP_DIR = "/app"
HARNESS_DIR = os.path.join(APP_DIR, "test", "odcontract")
HARNESS_FILE = os.path.join(HARNESS_DIR, "od_contract_test.go")

# Generous outer bound for the single `go test` invocation; the first run
# includes compiling the test binary. The verifier budget is 1800s.
GO_TEST_TIMEOUT_SEC = 1500

GO_HARNESS = r'''
// Package odcontract contains black-box contract tests for the xDS outlier
// detection load-balancing behavior. Each test spins up real backends plus a
// fake xDS management server, pushes resources with OutlierDetection
// settings, drives unary RPCs, and asserts the resulting traffic
// distribution.
package odcontract

import (
	"context"
	"errors"
	"fmt"
	"net"
	"strconv"
	"sync/atomic"
	"testing"
	"time"

	v3clusterpb "github.com/envoyproxy/go-control-plane/envoy/config/cluster/v3"
	v3endpointpb "github.com/envoyproxy/go-control-plane/envoy/config/endpoint/v3"
	v3listenerpb "github.com/envoyproxy/go-control-plane/envoy/config/listener/v3"
	v3routepb "github.com/envoyproxy/go-control-plane/envoy/config/route/v3"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/internal"
	"google.golang.org/grpc/internal/envconfig"
	"google.golang.org/grpc/internal/stubserver"
	"google.golang.org/grpc/internal/testutils/xds/e2e"
	testgrpc "google.golang.org/grpc/test/grpc_testing"
	testpb "google.golang.org/grpc/test/grpc_testing"
	// Registers the xds resolvers/balancers; transitively imports the
	// outlierdetection package (via xds/internal/balancer/clusterresolver)
	// so its init() installs the RegisterOutlierDetectionBalancerForTesting
	// hooks.
	_ "google.golang.org/grpc/xds"
	"google.golang.org/protobuf/types/known/durationpb"
	"google.golang.org/protobuf/types/known/wrapperspb"
)

// enableOutlierDetection flips the (still experimental) feature switch and
// registers the outlier detection LB policy, restoring both afterwards.
func enableOutlierDetection(t *testing.T) {
	t.Helper()
	old := envconfig.XDSOutlierDetection
	envconfig.XDSOutlierDetection = true
	internal.RegisterOutlierDetectionBalancerForTesting()
	t.Cleanup(func() {
		envconfig.XDSOutlierDetection = old
		internal.UnregisterOutlierDetectionBalancerForTesting()
	})
}

// testBackend is a local test server whose failure behavior can be flipped at
// runtime; it counts how many RPCs it serves.
type testBackend struct {
	port  uint32
	calls *int32
	fail  *int32
}

func startBackend(t *testing.T) *testBackend {
	t.Helper()
	var calls, fail int32
	ss := &stubserver.StubServer{
		EmptyCallF: func(context.Context, *testpb.Empty) (*testpb.Empty, error) {
			atomic.AddInt32(&calls, 1)
			if atomic.LoadInt32(&fail) == 1 {
				return nil, errors.New("injected backend error")
			}
			return &testpb.Empty{}, nil
		},
		Address: "localhost:0",
	}
	ss.StartServer()
	t.Cleanup(ss.Stop)
	_, p, err := net.SplitHostPort(ss.Address)
	if err != nil {
		t.Fatalf("invalid serving address for stub server: %v", err)
	}
	port, err := strconv.ParseUint(p, 10, 32)
	if err != nil {
		t.Fatalf("invalid serving port for stub server: %v", err)
	}
	return &testBackend{port: uint32(port), calls: &calls, fail: &fail}
}

func (b *testBackend) callCount() int64 {
	return int64(atomic.LoadInt32(b.calls))
}

func (b *testBackend) setFailing(failing bool) {
	if failing {
		atomic.StoreInt32(b.fail, 1)
		return
	}
	atomic.StoreInt32(b.fail, 0)
}

// failurePercentageOD builds an OutlierDetection config using the failure
// percentage algorithm with permissive volume/host gates so a single
// misbehaving backend qualifies within the first interval.
func failurePercentageOD(interval, baseEjection, maxEjection time.Duration, maxEjectionPercent uint32) *v3clusterpb.OutlierDetection {
	return &v3clusterpb.OutlierDetection{
		Interval:                       durationpb.New(interval),
		BaseEjectionTime:               durationpb.New(baseEjection),
		MaxEjectionTime:                durationpb.New(maxEjection),
		MaxEjectionPercent:             wrapperspb.UInt32(maxEjectionPercent),
		FailurePercentageThreshold:     wrapperspb.UInt32(50),
		EnforcingFailurePercentage:     wrapperspb.UInt32(100),
		FailurePercentageRequestVolume: wrapperspb.UInt32(1),
		FailurePercentageMinimumHosts:  wrapperspb.UInt32(1),
	}
}

// successRateOD builds an OutlierDetection config using the success rate
// algorithm with a 1.0 standard deviation factor.
func successRateOD(interval, baseEjection, maxEjection time.Duration) *v3clusterpb.OutlierDetection {
	return &v3clusterpb.OutlierDetection{
		Interval:                 durationpb.New(interval),
		BaseEjectionTime:         durationpb.New(baseEjection),
		MaxEjectionTime:          durationpb.New(maxEjection),
		MaxEjectionPercent:       wrapperspb.UInt32(100),
		SuccessRateStdevFactor:   wrapperspb.UInt32(1000),
		EnforcingSuccessRate:     wrapperspb.UInt32(100),
		SuccessRateRequestVolume: wrapperspb.UInt32(1),
		SuccessRateMinimumHosts:  wrapperspb.UInt32(1),
	}
}

// xdsEnv bundles everything a scenario needs to make RPCs.
type xdsEnv struct {
	client testgrpc.TestServiceClient
	cc     *grpc.ClientConn
}

// setupXDS starts a fake management server, pushes resources for one cluster
// with the given outlier detection config (nil means the cluster carries no
// OutlierDetection at all) covering the given backend ports, and dials it.
func setupXDS(t *testing.T, od *v3clusterpb.OutlierDetection, ports []uint32) xdsEnv {
	t.Helper()
	mgmtServer, nodeID, _, resolver, cleanup := e2e.SetupManagementServer(t, nil)
	t.Cleanup(cleanup)

	const serviceName = "od-contract-service"
	routeConfigName := "route-" + serviceName
	clusterName := "cluster-" + serviceName
	endpointsName := "endpoints-" + serviceName
	cluster := e2e.DefaultCluster(clusterName, endpointsName, e2e.SecurityLevelNone)
	cluster.OutlierDetection = od
	resources := e2e.UpdateOptions{
		NodeID:    nodeID,
		Listeners: []*v3listenerpb.Listener{e2e.DefaultClientListener(serviceName, routeConfigName)},
		Routes:    []*v3routepb.RouteConfiguration{e2e.DefaultRouteConfig(routeConfigName, serviceName, clusterName)},
		Clusters:  []*v3clusterpb.Cluster{cluster},
		Endpoints: []*v3endpointpb.ClusterLoadAssignment{e2e.DefaultEndpoint(endpointsName, "localhost", ports)},
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := mgmtServer.Update(ctx, resources); err != nil {
		t.Fatalf("management server update failed: %v", err)
	}

	cc, err := grpc.Dial(fmt.Sprintf("xds:///%s", serviceName), grpc.WithTransportCredentials(insecure.NewCredentials()), grpc.WithResolvers(resolver))
	if err != nil {
		t.Fatalf("failed to dial xds target: %v", err)
	}
	t.Cleanup(func() { cc.Close() })
	return xdsEnv{client: testgrpc.NewTestServiceClient(cc), cc: cc}
}

// driveTraffic issues unary RPCs for d and returns success/failure counts.
// Every RPC carries its own deadline so a broken data plane fails fast
// instead of hanging the test.
func driveTraffic(t *testing.T, client testgrpc.TestServiceClient, d time.Duration) (ok, failed int64) {
	t.Helper()
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		_, err := client.EmptyCall(ctx, &testpb.Empty{}, grpc.WaitForReady(true))
		cancel()
		if err != nil {
			failed++
		} else {
			ok++
		}
		time.Sleep(2 * time.Millisecond)
	}
	return ok, failed
}

// driveStep drives RPCs for d and reports the per-backend call deltas
// observed during the window alongside the RPC outcome counts.
func driveStep(t *testing.T, client testgrpc.TestServiceClient, d time.Duration, backends ...*testBackend) (deltas []int64, ok, failed int64) {
	t.Helper()
	before := make([]int64, len(backends))
	for i, b := range backends {
		before[i] = b.callCount()
	}
	ok, failed = driveTraffic(t, client, d)
	deltas = make([]int64, len(backends))
	for i, b := range backends {
		deltas[i] = b.callCount() - before[i]
	}
	return deltas, ok, failed
}

// waitForEjection drives RPCs until bad stops receiving traffic while the
// healthy backends keep serving, i.e. until the outlier detection balancer
// has ejected it.
func waitForEjection(t *testing.T, client testgrpc.TestServiceClient, healthy1, healthy2, bad *testBackend) {
	t.Helper()
	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		d, _, _ := driveStep(t, client, 200*time.Millisecond, healthy1, healthy2, bad)
		if d[0]+d[1] > 0 && d[2] == 0 {
			return
		}
	}
	t.Fatalf("failing backend was not ejected within 15s (healthy1=%d healthy2=%d bad=%d total calls)",
		healthy1.callCount(), healthy2.callCount(), bad.callCount())
}

// TestOutlierDetectionNoConfigRPCsSucceed verifies that with the feature
// enabled but no OutlierDetection config delivered by the management server,
// RPCs flow completely normally.
func TestOutlierDetectionNoConfigRPCsSucceed(t *testing.T) {
	enableOutlierDetection(t)
	b := startBackend(t)
	env := setupXDS(t, nil, []uint32{b.port})

	ok, failed := driveTraffic(t, env.client, 3*time.Second)
	if ok < 200 {
		t.Fatalf("too few successful RPCs with no outlier detection config: ok=%d failed=%d", ok, failed)
	}
	if failed != 0 {
		t.Fatalf("RPCs failed with no outlier detection config: failed=%d", failed)
	}
}

// TestOutlierDetectionFailurePercentageEjects verifies that a backend which
// consistently fails is ejected by the failure percentage algorithm and
// traffic shifts to the healthy backends.
func TestOutlierDetectionFailurePercentageEjects(t *testing.T) {
	enableOutlierDetection(t)
	b1 := startBackend(t)
	b2 := startBackend(t)
	bad := startBackend(t)
	bad.setFailing(true)
	od := failurePercentageOD(100*time.Millisecond, 30*time.Second, 300*time.Second, 100)
	env := setupXDS(t, od, []uint32{b1.port, b2.port, bad.port})

	waitForEjection(t, env.client, b1, b2, bad)

	d, ok, failed := driveStep(t, env.client, 3*time.Second, b1, b2, bad)
	total := d[0] + d[1] + d[2]
	if total < 200 {
		t.Fatalf("too few RPCs in measurement window: total=%d", total)
	}
	if d[2]*50 > total {
		t.Fatalf("ejected backend still receiving traffic: bad=%d of total=%d", d[2], total)
	}
	if d[0]*4 < total || d[1]*4 < total {
		t.Fatalf("traffic did not shift to the healthy backends: deltas=%v total=%d", d, total)
	}
	if failed*20 > ok {
		t.Fatalf("RPCs still failing after ejection: ok=%d failed=%d", ok, failed)
	}
}

// TestOutlierDetectionSuccessRateEjects verifies that a backend whose success
// rate is a statistical outlier is ejected by the success rate algorithm.
func TestOutlierDetectionSuccessRateEjects(t *testing.T) {
	enableOutlierDetection(t)
	b1 := startBackend(t)
	b2 := startBackend(t)
	bad := startBackend(t)
	bad.setFailing(true)
	od := successRateOD(100*time.Millisecond, 30*time.Second, 300*time.Second)
	env := setupXDS(t, od, []uint32{b1.port, b2.port, bad.port})

	waitForEjection(t, env.client, b1, b2, bad)

	d, ok, failed := driveStep(t, env.client, 3*time.Second, b1, b2, bad)
	total := d[0] + d[1] + d[2]
	if total < 200 {
		t.Fatalf("too few RPCs in measurement window: total=%d", total)
	}
	if d[2]*50 > total {
		t.Fatalf("ejected backend still receiving traffic: bad=%d of total=%d", d[2], total)
	}
	if d[0]*4 < total || d[1]*4 < total {
		t.Fatalf("traffic did not shift to the healthy backends: deltas=%v total=%d", d, total)
	}
	if failed*20 > ok {
		t.Fatalf("RPCs still failing after ejection: ok=%d failed=%d", ok, failed)
	}
}

// TestOutlierDetectionUnejectAfterEjectionTime verifies that an ejected
// backend is automatically unejected once its ejection time elapses, and
// goes back to sharing the load once healthy again.
func TestOutlierDetectionUnejectAfterEjectionTime(t *testing.T) {
	enableOutlierDetection(t)
	b1 := startBackend(t)
	b2 := startBackend(t)
	bad := startBackend(t)
	bad.setFailing(true)
	od := failurePercentageOD(100*time.Millisecond, 3*time.Second, 30*time.Second, 100)
	env := setupXDS(t, od, []uint32{b1.port, b2.port, bad.port})

	waitForEjection(t, env.client, b1, b2, bad)

	// Heal the backend right away, well before the base ejection time (3s)
	// elapses, so the first uneject finds it healthy.
	bad.setFailing(false)

	d, ok, failed := driveStep(t, env.client, 8*time.Second, b1, b2, bad)
	total := d[0] + d[1] + d[2]
	if total < 200 {
		t.Fatalf("too few RPCs in recovery window: total=%d", total)
	}
	// Unejection happens about 3s after the ejection, so the recovered
	// backend must serve a clear share of the 8s window.
	if d[2]*8 < total {
		t.Fatalf("ejected backend did not recover after the ejection time: bad=%d of total=%d", d[2], total)
	}
	if failed*20 > ok {
		t.Fatalf("RPCs still failing after recovery: ok=%d failed=%d", ok, failed)
	}
}

// TestOutlierDetectionMaxEjectionPercentCaps verifies that the
// max_ejection_percent knob caps how many addresses may be ejected at once:
// with two of three backends failing and a 33% cap, exactly one of them may
// be ejected, so the healthy backend shares traffic with the other failing
// one instead of taking everything.
func TestOutlierDetectionMaxEjectionPercentCaps(t *testing.T) {
	enableOutlierDetection(t)
	good := startBackend(t)
	bad1 := startBackend(t)
	bad2 := startBackend(t)
	bad1.setFailing(true)
	bad2.setFailing(true)
	od := failurePercentageOD(100*time.Millisecond, 30*time.Second, 300*time.Second, 33)
	env := setupXDS(t, od, []uint32{good.port, bad1.port, bad2.port})

	// Wait for the steady state: exactly one of the two failing backends
	// ejected (the healthy one keeps serving throughout).
	settled := false
	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		d, _, _ := driveStep(t, env.client, 200*time.Millisecond, good, bad1, bad2)
		if d[0] > 0 && (d[1] == 0) != (d[2] == 0) {
			settled = true
			break
		}
	}
	if !settled {
		t.Fatalf("never reached a state with exactly one failing backend ejected (good=%d bad1=%d bad2=%d total calls)",
			good.callCount(), bad1.callCount(), bad2.callCount())
	}

	d, ok, failed := driveStep(t, env.client, 3*time.Second, good, bad1, bad2)
	total := d[0] + d[1] + d[2]
	if total < 200 {
		t.Fatalf("too few RPCs in measurement window: total=%d", total)
	}
	if d[0]*5 < total*2 {
		t.Fatalf("healthy backend starved (%d of %d); no failing backend was ejected", d[0], total)
	}
	if d[0]*4 > total*3 {
		t.Fatalf("healthy backend took all traffic (%d of %d); max_ejection_percent cap not enforced", d[0], total)
	}
	if failed*3 < ok {
		t.Fatalf("expected a substantial share of RPCs to still hit a failing backend: ok=%d failed=%d", ok, failed)
	}
}
'''


@pytest.fixture(scope="session", autouse=True)
def harness_results():
    """Install the Go contract-test harness into /app and run it once."""
    os.makedirs(HARNESS_DIR, exist_ok=True)
    with open(HARNESS_FILE, "w") as fh:
        fh.write(GO_HARNESS)
    proc = subprocess.run(
        ["go", "test", "-count=1", "-json", "-timeout", "1200s",
         "-run", "TestOutlierDetection", "./test/odcontract/"],
        cwd=APP_DIR, capture_output=True, text=True,
        timeout=GO_TEST_TIMEOUT_SEC,
    )
    results = {}
    tail = []
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        action = event.get("Action")
        if action == "output":
            tail.append(event.get("Output", ""))
            del tail[:-120]
        test = event.get("Test")
        if test and action in ("pass", "fail", "skip"):
            results[test] = action
    return {
        "returncode": proc.returncode,
        "results": results,
        "tail": "".join(tail),
        "stderr": proc.stderr[-2000:],
    }


def _assert_go_test(harness_results, name):
    info = harness_results
    assert name in info["results"], (
        "go 行为用例 {0} 没有产生结果（go test 退出码 {1}；被测实现编译失败或运行时"
        "崩溃都会导致这一结果）。\n---- go test 输出尾部 ----\n{2}\n"
        "---- stderr ----\n{3}".format(
            name, info["returncode"], info["tail"], info["stderr"]))
    assert info["results"][name] == "pass", (
        "go 行为用例 {0} 未通过。\n---- go test 输出尾部 ----\n{1}".format(
            name, info["tail"]))


def test_noop_config_rpcs_succeed(harness_results):
    """无 OutlierDetection 配置时 RPC 完全正常（noop 配置不伤人）。"""
    _assert_go_test(harness_results, "TestOutlierDetectionNoConfigRPCsSucceed")


def test_failure_percentage_ejects_failing_backend(harness_results):
    """failure_percentage 算法剔除持续失败的后端，流量转移到健康后端。"""
    _assert_go_test(harness_results, "TestOutlierDetectionFailurePercentageEjects")


def test_success_rate_ejects_outlier_backend(harness_results):
    """success_rate 算法剔除成功率显著偏离群体的后端。"""
    _assert_go_test(harness_results, "TestOutlierDetectionSuccessRateEjects")


def test_uneject_after_ejection_time(harness_results):
    """剔除时长到期后自动恢复，康复的后端重新分担流量。"""
    _assert_go_test(harness_results, "TestOutlierDetectionUnejectAfterEjectionTime")


def test_max_ejection_percent_caps_ejections(harness_results):
    """max_ejection_percent 限制同时被剔除的地址占比上限。"""
    _assert_go_test(harness_results, "TestOutlierDetectionMaxEjectionPercentCaps")
