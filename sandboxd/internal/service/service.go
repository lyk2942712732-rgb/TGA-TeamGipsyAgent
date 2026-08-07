package service

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"

	sandboxv1 "github.com/team-gipsy/tga-sandboxd/api/sandbox/v1"
	"github.com/team-gipsy/tga-sandboxd/internal/config"
	"github.com/team-gipsy/tga-sandboxd/internal/network"
	runtimepkg "github.com/team-gipsy/tga-sandboxd/internal/runtime"
)

const Version = "0.1.0"

type Service struct {
	sandboxv1.UnimplementedSandboxServiceServer
	config  *config.Config
	runtime *runtimepkg.Runtime
	network *network.Policy
	locksMu sync.Mutex
	locks   map[string]*taskExecutionLock
}

type taskExecutionLock struct {
	mu   sync.RWMutex
	refs int
}

func New(cfg *config.Config, runtime *runtimepkg.Runtime, policy *network.Policy) *Service {
	return &Service{
		config: cfg, runtime: runtime, network: policy,
		locks: make(map[string]*taskExecutionLock),
	}
}

func (s *Service) Health(ctx context.Context, request *sandboxv1.HealthRequest) (*sandboxv1.HealthResponse, error) {
	if request.ProtocolMajor != s.config.Sandboxd.ProtocolMajor {
		return nil, errors.New("protocol major mismatch")
	}
	if request.ConfigDigest != s.config.Digest {
		return nil, errors.New("configuration digest mismatch")
	}
	info, healthErr := s.runtime.Health(ctx)
	if healthErr != nil && info.ImageStoreError == "" {
		info.ImageStoreError = healthErr.Error()
	}
	return &sandboxv1.HealthResponse{
		ProtocolMajor: 1, DaemonVersion: Version,
		DockerAvailable:        healthErr == nil,
		RunscAvailable:         commandAvailable(ctx, "runsc"),
		NftablesAvailable:      s.network.Available(ctx),
		CgroupV2Available:      fileExists("/sys/fs/cgroup/cgroup.controllers"),
		ConfigDigest:           s.config.Digest,
		DockerApiVersion:       info.DockerAPIVersion,
		RunscRuntimeRegistered: info.RunscRuntimeRegistered,
		ClientUidPolicyActive:  len(s.config.Sandboxd.AllowedClientUIDs) > 0,
		ImageStoreReadable:     info.ImageStoreReadable,
		LocalImageDigests:      info.LocalImageDigests,
		ImageStoreError:        info.ImageStoreError,
	}, nil
}

func (s *Service) Acquire(ctx context.Context, request *sandboxv1.AcquireRequest) (*sandboxv1.AcquireResponse, error) {
	instance, reused, err := s.runtime.Acquire(
		ctx, request.TaskId, request.SolverId, request.SolverRunId, request.ProfileId,
		request.ConfigDigest, request.FencingToken,
	)
	if err != nil {
		return nil, err
	}
	return &sandboxv1.AcquireResponse{
		InstanceId: instance.ID, ConfigDigest: instance.ConfigDigest,
		FencingToken: instance.FencingToken, Reused: reused,
		ImageDigest:   imageDigest(s.config.Profiles[request.ProfileId].Image),
		ToolsetDigest: s.config.Profiles[request.ProfileId].ToolsetDigest,
	}, nil
}

func imageDigest(image string) string {
	const marker = "@sha256:"
	index := strings.LastIndex(image, marker)
	if index < 0 {
		return ""
	}
	return image[index+len(marker):]
}

func (s *Service) Exec(request *sandboxv1.ExecRequest, stream sandboxv1.SandboxService_ExecServer) (returnErr error) {
	releaseExecution := s.lockTaskExecution(request.InstanceId, true)
	defer releaseExecution()
	profile, err := s.profileForInstance(stream.Context(), request.InstanceId, request.FencingToken)
	if err != nil {
		return err
	}
	spec, err := processSpec(request.Process, profile, request.SolverId)
	if err != nil {
		return err
	}
	if err := s.applyNetwork(stream.Context(), request.InstanceId, request.FencingToken, request.Process.NetworkGrants); err != nil {
		return err
	}
	defer func() {
		returnErr = errors.Join(returnErr, s.resetNetwork(request.InstanceId, request.FencingToken))
	}()
	result, err := s.runtime.Exec(stream.Context(), request.InstanceId, request.FencingToken, spec, func(frame runtimepkg.Frame) error {
		kind := sandboxv1.ExecFrame_STDOUT
		if frame.Stderr {
			kind = sandboxv1.ExecFrame_STDERR
		}
		return stream.Send(&sandboxv1.ExecEvent{Event: &sandboxv1.ExecEvent_Frame{Frame: &sandboxv1.ExecFrame{
			Sequence: frame.Sequence, TimestampUnixMs: frame.Timestamp.UnixMilli(),
			Stream: kind, Data: frame.Data,
		}}})
	})
	if err != nil {
		return err
	}
	value := &sandboxv1.ExecResult{Signal: result.Signal, TimedOut: result.TimedOut, Truncated: result.Truncated}
	if result.ExitCode != nil {
		value.ExitCode = result.ExitCode
	}
	return stream.Send(&sandboxv1.ExecEvent{Event: &sandboxv1.ExecEvent_Result{Result: value}})
}

func (s *Service) OpenProcess(stream sandboxv1.SandboxService_OpenProcessServer) (returnErr error) {
	first, err := stream.Recv()
	if err != nil {
		return err
	}
	start := first.GetStart()
	if start == nil {
		return errors.New("first process message must be start")
	}
	releaseExecution := s.lockTaskExecution(start.InstanceId, true)
	defer releaseExecution()
	profile, err := s.profileForInstance(stream.Context(), start.InstanceId, start.FencingToken)
	if err != nil {
		return err
	}
	spec, err := processSpec(start.Process, profile, start.SolverId)
	if err != nil {
		return err
	}
	if err := s.applyNetwork(stream.Context(), start.InstanceId, start.FencingToken, start.Process.NetworkGrants); err != nil {
		return err
	}
	defer func() {
		returnErr = errors.Join(returnErr, s.resetNetwork(start.InstanceId, start.FencingToken))
	}()
	process, err := s.runtime.OpenProcess(stream.Context(), start.InstanceId, start.FencingToken, spec, func(frame runtimepkg.Frame) error {
		kind := sandboxv1.ExecFrame_STDOUT
		if frame.Stderr {
			kind = sandboxv1.ExecFrame_STDERR
		}
		return stream.Send(&sandboxv1.ProcessMessage{Message: &sandboxv1.ProcessMessage_Frame{
			Frame: &sandboxv1.ExecFrame{Sequence: frame.Sequence, TimestampUnixMs: frame.Timestamp.UnixMilli(), Stream: kind, Data: frame.Data},
		}})
	})
	if err != nil {
		return err
	}
	if err := stream.Send(&sandboxv1.ProcessMessage{Message: &sandboxv1.ProcessMessage_Opened{
		Opened: &sandboxv1.ProcessOpened{ProcessId: process.ID},
	}}); err != nil {
		return err
	}
	inputDone := make(chan error, 1)
	go func() {
		for {
			message, err := stream.Recv()
			if err != nil {
				if errors.Is(err, io.EOF) {
					_ = process.CloseStdin()
					inputDone <- nil
				} else {
					inputDone <- err
				}
				return
			}
			if resize := message.GetResize(); resize != nil {
				if err := s.runtime.ResizeProcess(stream.Context(), process.ID, start.FencingToken, resize.Cols, resize.Rows); err != nil {
					inputDone <- err
					return
				}
				continue
			}
			input := message.GetInput()
			if input == nil {
				inputDone <- errors.New("only input or resize messages are accepted after start")
				return
			}
			if len(input.Data) > 0 {
				if err := process.Send(input.Data); err != nil {
					inputDone <- err
					return
				}
			}
			if input.CloseStdin {
				inputDone <- process.CloseStdin()
				return
			}
		}
	}()
	result, err := process.Wait(stream.Context())
	if err != nil && !errors.Is(err, io.EOF) {
		return err
	}
	value := &sandboxv1.ExecResult{Signal: result.Signal, TimedOut: result.TimedOut, Truncated: result.Truncated}
	if result.ExitCode != nil {
		value.ExitCode = result.ExitCode
	}
	return stream.Send(&sandboxv1.ProcessMessage{Message: &sandboxv1.ProcessMessage_Result{Result: value}})
}

func (s *Service) StopProcess(ctx context.Context, request *sandboxv1.StopProcessRequest) (*sandboxv1.Empty, error) {
	return &sandboxv1.Empty{}, s.runtime.StopProcess(ctx, request.ProcessId, request.FencingToken)
}

func (s *Service) Inspect(ctx context.Context, request *sandboxv1.InspectRequest) (*sandboxv1.InspectResponse, error) {
	instance, active, err := s.runtime.Inspect(ctx, request.InstanceId, request.FencingToken)
	if err != nil {
		return nil, err
	}
	return &sandboxv1.InspectResponse{State: "ready", Runtime: "runsc", ActiveProcesses: uint32(active), CreatedAt: instance.CreatedAt}, nil
}

func (s *Service) Destroy(ctx context.Context, request *sandboxv1.DestroyRequest) (*sandboxv1.Empty, error) {
	releaseExecution := s.lockTaskExecution(request.InstanceId, true)
	defer releaseExecution()
	cleanupTaskID, cleanupRunID := request.TaskId, request.SolverRunId
	if !config.ValidIdentifier(cleanupTaskID) || !config.ValidIdentifier(cleanupRunID) {
		return nil, errors.New("destroy requires valid task and SolverRun ids")
	}
	if instance, _, err := s.runtime.Inspect(ctx, request.InstanceId, request.FencingToken); err == nil {
		cleanupTaskID, cleanupRunID = instance.TaskID, instance.SolverRunID
	} else if !runtimepkg.IsNotFound(err) {
		return nil, err
	}
	// Delete policy first.  If container removal then fails, the operation is
	// safe to retry; doing this in the opposite order loses the instance ID
	// needed to remove an nft table after a daemon/network failure.
	if err := s.network.Delete(ctx, cleanupTaskID+"\x00"+cleanupRunID); err != nil {
		return nil, err
	}
	if err := s.runtime.Destroy(
		ctx, request.InstanceId, request.FencingToken, cleanupTaskID, cleanupRunID,
	); err != nil {
		return nil, err
	}
	return &sandboxv1.Empty{}, nil
}

func (s *Service) Reconcile(ctx context.Context, request *sandboxv1.ReconcileRequest) (*sandboxv1.ReconcileResponse, error) {
	valid := make(map[string]struct{}, len(request.ValidInstanceIds))
	for _, id := range request.ValidInstanceIds {
		valid[id] = struct{}{}
	}
	destroyed, active, failures := s.runtime.Reconcile(ctx, valid, time.UnixMilli(request.GraceBeforeUnixMs))
	response := &sandboxv1.ReconcileResponse{}
	for _, instance := range destroyed {
		response.DestroyedInstanceIds = append(response.DestroyedInstanceIds, instance.ID)
		if err := s.network.Delete(ctx, instance.TaskID+"\x00"+instance.SolverRunID); err != nil {
			failures = append(failures, err)
		}
	}
	if _, err := s.network.Reconcile(ctx, active); err != nil {
		failures = append(failures, err)
	}
	for _, failure := range failures {
		response.Errors = append(response.Errors, failure.Error())
	}
	return response, nil
}

func (s *Service) resetNetwork(instanceID string, fencing uint64) error {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if err := s.applyNetwork(ctx, instanceID, fencing, nil); err != nil {
		quarantineErr := s.runtime.Quarantine(ctx, instanceID)
		return errors.Join(fmt.Errorf("reset sandbox network policy: %w", err), quarantineErr)
	}
	return nil
}

func (s *Service) profileForInstance(ctx context.Context, id string, fencing uint64) (config.Profile, error) {
	instance, _, err := s.runtime.Inspect(ctx, id, fencing)
	if err != nil {
		return config.Profile{}, err
	}
	return s.config.Profile(instance.ProfileID)
}

func processSpec(value *sandboxv1.ProcessSpec, profile config.Profile, solverID string) (runtimepkg.ProcessSpec, error) {
	if value == nil {
		return runtimepkg.ProcessSpec{}, errors.New("process is required")
	}
	if !config.ValidIdentifier(solverID) {
		return runtimepkg.ProcessSpec{}, errors.New("invalid solver id")
	}
	// argv[0] must always be present and allowlisted by the Profile. There is
	// no tool mapping that could supply an image or entrypoint instead.
	if len(value.Argv) == 0 {
		return runtimepkg.ProcessSpec{}, errors.New("process requires argv")
	}
	allowed := false
	for _, executable := range profile.AllowedExecutables {
		if value.Argv[0] == executable {
			allowed = true
			break
		}
	}
	if !allowed {
		return runtimepkg.ProcessSpec{}, errors.New("executable is not allowed by sandbox profile")
	}
	timeout := value.TimeoutSeconds
	if timeout == 0 || int(timeout) > profile.Limits.TimeoutSeconds {
		timeout = uint32(profile.Limits.TimeoutSeconds)
	}
	for _, grant := range value.NetworkGrants {
		if err := config.ValidateCIDR(grant.Cidr); err != nil {
			return runtimepkg.ProcessSpec{}, err
		}
		for _, port := range grant.Ports {
			if port == 0 || port > 65535 {
				return runtimepkg.ProcessSpec{}, errors.New("invalid port")
			}
		}
	}
	return runtimepkg.ProcessSpec{
		Argv: value.Argv, Environment: value.Environment,
		LogicalWorkspace: value.LogicalWorkspace, WorkingDirectory: value.WorkingDirectory,
		Stdin: append([]byte(nil), value.Stdin...), Interactive: value.Interactive,
		Timeout:        time.Duration(timeout) * time.Second,
		MaxOutputBytes: profile.Limits.MaxOutputBytes,
		SolverID:       solverID,
		ToolID:         value.ToolId,
	}, nil
}

func (s *Service) applyNetwork(
	ctx context.Context,
	instanceID string,
	fencing uint64,
	values []*sandboxv1.NetworkGrant,
) error {
	policyID, bridge, gateways, err := s.runtime.NetworkPolicyContext(ctx, instanceID, fencing)
	if err != nil {
		return err
	}
	grants := make([]network.Grant, 0, len(values))
	for _, value := range values {
		if err := config.ValidateCIDR(value.Cidr); err != nil {
			return err
		}
		grants = append(grants, network.Grant{CIDR: value.Cidr, Ports: value.Ports})
	}
	return s.network.Apply(ctx, policyID, bridge, gateways, grants)
}

func commandAvailable(ctx context.Context, name string) bool {
	return exec.CommandContext(ctx, name, "--version").Run() == nil
}
func fileExists(path string) bool { _, err := os.Stat(path); return err == nil }

func (s *Service) lockTaskExecution(instanceID string, exclusive bool) func() {
	s.locksMu.Lock()
	lock := s.locks[instanceID]
	if lock == nil {
		lock = &taskExecutionLock{}
		s.locks[instanceID] = lock
	}
	lock.refs++
	s.locksMu.Unlock()

	if exclusive {
		lock.mu.Lock()
	} else {
		lock.mu.RLock()
	}
	return func() {
		if exclusive {
			lock.mu.Unlock()
		} else {
			lock.mu.RUnlock()
		}
		s.locksMu.Lock()
		lock.refs--
		if lock.refs == 0 {
			delete(s.locks, instanceID)
		}
		s.locksMu.Unlock()
	}
}
