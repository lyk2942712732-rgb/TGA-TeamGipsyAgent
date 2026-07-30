package runtime

import (
	"bufio"
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/moby/moby/api/pkg/stdcopy"
	"github.com/moby/moby/api/types/container"
	"github.com/moby/moby/api/types/mount"
	"github.com/moby/moby/api/types/network"
	"github.com/moby/moby/client"

	"github.com/team-gipsy/tga-sandboxd/internal/config"
)

const (
	LabelManaged = "tga.sandbox.managed"
	LabelTask    = "tga.sandbox.task"
	LabelSolver  = "tga.sandbox.solver"
	LabelProfile = "tga.sandbox.profile"
	LabelDigest  = "tga.sandbox.config"
	LabelFencing = "tga.sandbox.fencing"
	LabelKind    = "tga.sandbox.kind"
)

type Instance struct {
	ID           string
	TaskID       string
	SolverID     string
	ProfileID    string
	ConfigDigest string
	FencingToken uint64
	CreatedAt    string
}

type ProcessSpec struct {
	Argv             []string
	Environment      map[string]string
	LogicalWorkspace string
	Timeout          time.Duration
	MaxOutputBytes   int64
	SolverID         string
	ToolID           string
}

type Frame struct {
	Sequence  uint64
	Timestamp time.Time
	Stderr    bool
	Data      []byte
}

type Result struct {
	ExitCode  *int32
	Signal    string
	TimedOut  bool
	Truncated bool
}

type HealthInfo struct {
	DockerAPIVersion       string
	RunscRuntimeRegistered bool
}

type Runtime struct {
	docker    *client.Client
	config    *config.Config
	mu        sync.Mutex
	processes sync.Map
}

type Process struct {
	ID           string
	InstanceID   string
	FencingToken uint64
	execID       string
	containerID  string
	attached     client.HijackedResponse
	done         chan struct{}
	result       Result
	err          error
}

func New(cfg *config.Config) (*Runtime, error) {
	api, err := client.New(client.FromEnv, client.WithUserAgent("tga-sandboxd/0.1.0"))
	if err != nil {
		return nil, err
	}
	if _, err := api.Ping(context.Background(), client.PingOptions{NegotiateAPIVersion: true}); err != nil {
		return nil, fmt.Errorf("docker ping: %w", err)
	}
	return &Runtime{docker: api, config: cfg}, nil
}

func (r *Runtime) Close() error { return r.docker.Close() }

func (r *Runtime) Acquire(
	ctx context.Context,
	taskID, solverID, profileID, digest string,
	fencing uint64,
) (Instance, bool, error) {
	if !config.ValidIdentifier(taskID) || !config.ValidIdentifier(solverID) {
		return Instance{}, false, errors.New("invalid task or solver id")
	}
	profile, err := r.config.Profile(profileID)
	if err != nil {
		return Instance{}, false, err
	}
	if digest != r.config.Digest {
		return Instance{}, false, errors.New("config digest mismatch")
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	existing, err := r.byTask(ctx, taskID)
	if err != nil {
		return Instance{}, false, err
	}
	if existing.ID != "" {
		if existing.ConfigDigest != digest || existing.ProfileID != profileID {
			return Instance{}, false, errors.New("active task sandbox conflicts with request")
		}
		if fencing < existing.FencingToken {
			return Instance{}, false, errors.New("stale fencing token")
		}
		return existing, true, nil
	}
	workspace, err := r.config.Workspace(taskID)
	if err != nil {
		return Instance{}, false, err
	}
	if err := os.MkdirAll(workspace, 0o750); err != nil {
		return Instance{}, false, err
	}
	labels := labels(taskID, solverID, profileID, digest, fencing)
	labels[LabelKind] = "sandbox"
	networkName, bridgeName := taskNetwork(taskID)
	networkCreated, err := r.docker.NetworkCreate(ctx, networkName, client.NetworkCreateOptions{
		Driver:  "bridge",
		Options: map[string]string{"com.docker.network.bridge.name": bridgeName},
		Labels:  labels,
	})
	if err != nil {
		return Instance{}, false, fmt.Errorf("create task network: %w", err)
	}
	caps := []string{}
	if profile.AllowNetRaw {
		caps = append(caps, "NET_RAW")
	}
	memory := profile.Limits.MemoryBytes
	if memory == 0 {
		memory = 512 * 1024 * 1024
	}
	pids := profile.Limits.PidsLimit
	if pids == 0 {
		pids = 256
	}
	cpu := int64(profile.Limits.CPUCount * 1_000_000_000)
	if cpu == 0 {
		cpu = 1_000_000_000
	}
	created, err := r.docker.ContainerCreate(ctx, client.ContainerCreateOptions{
		Config: &container.Config{
			Image:           profile.Image,
			Cmd:             []string{"/usr/bin/tail", "-f", "/dev/null"},
			WorkingDir:      "/workspace",
			User:            "tga",
			Labels:          labels,
			NetworkDisabled: false,
		},
		HostConfig: &container.HostConfig{
			Runtime:        "runsc",
			ReadonlyRootfs: true,
			CapDrop:        []string{"ALL"},
			CapAdd:         caps,
			SecurityOpt:    []string{"no-new-privileges:true"},
			Resources: container.Resources{
				Memory:    memory,
				NanoCPUs:  cpu,
				PidsLimit: &pids,
			},
			Mounts: []mount.Mount{{
				Type: mount.TypeBind, Source: workspace, Target: "/workspace",
			}},
			Tmpfs: map[string]string{"/tmp": "rw,noexec,nosuid,size=67108864,mode=1777"},
		},
		NetworkingConfig: &network.NetworkingConfig{EndpointsConfig: map[string]*network.EndpointSettings{
			networkName: {},
		}},
		Name: "tga-" + taskID,
	})
	if err != nil {
		_, _ = r.docker.NetworkRemove(context.Background(), networkCreated.ID, client.NetworkRemoveOptions{})
		return Instance{}, false, err
	}
	if _, err := r.docker.ContainerStart(ctx, created.ID, client.ContainerStartOptions{}); err != nil {
		_, _ = r.docker.ContainerRemove(context.Background(), created.ID, client.ContainerRemoveOptions{Force: true})
		_, _ = r.docker.NetworkRemove(context.Background(), networkCreated.ID, client.NetworkRemoveOptions{})
		return Instance{}, false, err
	}
	return Instance{
		ID: created.ID, TaskID: taskID, SolverID: solverID, ProfileID: profileID,
		ConfigDigest: digest, FencingToken: fencing, CreatedAt: time.Now().UTC().Format(time.RFC3339Nano),
	}, false, nil
}

func (r *Runtime) Exec(
	ctx context.Context,
	instanceID string,
	fencing uint64,
	spec ProcessSpec,
	emit func(Frame) error,
) (Result, error) {
	if err := r.validate(ctx, instanceID, fencing); err != nil {
		return Result{}, err
	}
	if err := validateSpec(spec); err != nil {
		return Result{}, err
	}
	env := make([]string, 0, len(spec.Environment))
	for key, value := range spec.Environment {
		env = append(env, key+"="+value)
	}
	response, err := r.docker.ExecCreate(ctx, instanceID, client.ExecCreateOptions{
		Cmd: spec.Argv, Env: env, WorkingDir: workspace(spec.LogicalWorkspace, spec.SolverID),
		AttachStdout: true, AttachStderr: true,
	})
	if err != nil {
		return Result{}, err
	}
	attached, err := r.docker.ExecAttach(ctx, response.ID, client.ExecAttachOptions{})
	if err != nil {
		return Result{}, err
	}
	defer attached.Close()
	runCtx := ctx
	cancel := func() {}
	if spec.Timeout > 0 {
		runCtx, cancel = context.WithTimeout(ctx, spec.Timeout)
	}
	defer cancel()
	var sequence atomic.Uint64
	var total atomic.Int64
	var truncated atomic.Bool
	writer := func(stderr bool) io.Writer {
		return &frameWriter{write: func(data []byte) error {
			remaining := spec.MaxOutputBytes - total.Load()
			if remaining <= 0 {
				truncated.Store(true)
				return nil
			}
			if int64(len(data)) > remaining {
				data = data[:remaining]
				truncated.Store(true)
			}
			total.Add(int64(len(data)))
			return emit(Frame{Sequence: sequence.Add(1), Timestamp: time.Now(), Stderr: stderr, Data: append([]byte(nil), data...)})
		}}
	}
	copyDone := make(chan error, 1)
	go func() {
		_, err := stdcopy.StdCopy(writer(false), writer(true), bufio.NewReader(attached.Reader))
		copyDone <- err
	}()
	select {
	case err := <-copyDone:
		if err != nil && !errors.Is(err, io.EOF) {
			return Result{}, err
		}
	case <-runCtx.Done():
		// Docker cannot reliably signal an individual exec process. Killing the
		// task container is the fail-closed cleanup boundary.
		_, _ = r.docker.ContainerKill(context.Background(), instanceID, client.ContainerKillOptions{Signal: "KILL"})
		return Result{TimedOut: true, Truncated: truncated.Load()}, nil
	}
	inspected, err := r.docker.ExecInspect(ctx, response.ID, client.ExecInspectOptions{})
	if err != nil {
		return Result{}, err
	}
	code := int32(inspected.ExitCode)
	return Result{ExitCode: &code, Truncated: truncated.Load()}, nil
}

func (r *Runtime) OpenProcess(
	ctx context.Context,
	instanceID string,
	fencing uint64,
	spec ProcessSpec,
	emit func(Frame) error,
) (*Process, error) {
	if err := r.validate(ctx, instanceID, fencing); err != nil {
		return nil, err
	}
	if err := validateSpec(spec); err != nil {
		return nil, err
	}
	if spec.ToolID != "" {
		return r.openToolProcess(ctx, instanceID, fencing, spec, emit)
	}
	env := make([]string, 0, len(spec.Environment))
	for key, value := range spec.Environment {
		env = append(env, key+"="+value)
	}
	created, err := r.docker.ExecCreate(ctx, instanceID, client.ExecCreateOptions{
		Cmd: spec.Argv, Env: env, WorkingDir: workspace(spec.LogicalWorkspace, spec.SolverID),
		AttachStdin: true, AttachStdout: true, AttachStderr: true,
	})
	if err != nil {
		return nil, err
	}
	attached, err := r.docker.ExecAttach(ctx, created.ID, client.ExecAttachOptions{})
	if err != nil {
		return nil, err
	}
	process := &Process{
		ID: fmt.Sprintf("p-%s", created.ID[:12]), InstanceID: instanceID,
		FencingToken: fencing, execID: created.ID, attached: attached.HijackedResponse,
		done: make(chan struct{}),
	}
	r.processes.Store(process.ID, process)
	if spec.Timeout > 0 {
		time.AfterFunc(spec.Timeout, func() {
			_ = r.StopProcess(context.Background(), process.ID, fencing)
		})
	}
	go func() {
		defer close(process.done)
		defer r.processes.Delete(process.ID)
		defer attached.Close()
		var sequence atomic.Uint64
		var total atomic.Int64
		var truncated atomic.Bool
		writer := func(stderr bool) io.Writer {
			return &frameWriter{write: func(data []byte) error {
				remaining := spec.MaxOutputBytes - total.Load()
				if remaining <= 0 {
					truncated.Store(true)
					return nil
				}
				if int64(len(data)) > remaining {
					data = data[:remaining]
					truncated.Store(true)
				}
				total.Add(int64(len(data)))
				return emit(Frame{Sequence: sequence.Add(1), Timestamp: time.Now(), Stderr: stderr, Data: append([]byte(nil), data...)})
			}}
		}
		_, process.err = stdcopy.StdCopy(writer(false), writer(true), attached.Reader)
		inspected, inspectErr := r.docker.ExecInspect(context.Background(), created.ID, client.ExecInspectOptions{})
		if process.err == nil {
			process.err = inspectErr
		}
		if inspectErr == nil {
			code := int32(inspected.ExitCode)
			process.result.ExitCode = &code
		}
		process.result.Truncated = truncated.Load()
	}()
	return process, nil
}

func (r *Runtime) openToolProcess(
	ctx context.Context,
	instanceID string,
	fencing uint64,
	spec ProcessSpec,
	emit func(Frame) error,
) (*Process, error) {
	instance, _, err := r.Inspect(ctx, instanceID, fencing)
	if err != nil {
		return nil, err
	}
	profile, err := r.config.Profile(instance.ProfileID)
	if err != nil {
		return nil, err
	}
	tool, err := r.config.Tool(spec.ToolID, instance.ProfileID)
	if err != nil {
		return nil, err
	}
	root, err := r.config.Workspace(instance.TaskID)
	if err != nil {
		return nil, err
	}
	env := make([]string, 0, len(spec.Environment))
	for key, value := range spec.Environment {
		env = append(env, key+"="+value)
	}
	caps := []string{}
	if profile.AllowNetRaw {
		caps = append(caps, "NET_RAW")
	}
	networkName, _ := taskNetwork(instance.TaskID)
	toolLabels := labels(instance.TaskID, spec.SolverID, instance.ProfileID, instance.ConfigDigest, fencing)
	toolLabels[LabelKind] = "tool"
	created, err := r.docker.ContainerCreate(ctx, client.ContainerCreateOptions{
		Config: &container.Config{
			Image:        tool.Image,
			Cmd:          append(append([]string{}, tool.Args...), spec.Argv...),
			Env:          env,
			WorkingDir:   workspace(spec.LogicalWorkspace, spec.SolverID),
			User:         "10001:10001",
			Labels:       toolLabels,
			OpenStdin:    true,
			StdinOnce:    false,
			AttachStdin:  true,
			AttachStdout: true,
			AttachStderr: true,
		},
		HostConfig: &container.HostConfig{
			Runtime:        "runsc",
			ReadonlyRootfs: true,
			CapDrop:        []string{"ALL"},
			CapAdd:         caps,
			SecurityOpt:    []string{"no-new-privileges:true"},
			Resources: container.Resources{
				Memory:    profile.Limits.MemoryBytes,
				NanoCPUs:  int64(profile.Limits.CPUCount * 1_000_000_000),
				PidsLimit: &profile.Limits.PidsLimit,
			},
			Mounts: []mount.Mount{{
				Type: mount.TypeBind, Source: root, Target: "/workspace",
			}},
			Tmpfs: map[string]string{"/tmp": "rw,noexec,nosuid,size=67108864,mode=1777"},
		},
		NetworkingConfig: &network.NetworkingConfig{EndpointsConfig: map[string]*network.EndpointSettings{
			networkName: {},
		}},
		Name: fmt.Sprintf("tga-tool-%x", sha256.Sum256([]byte(instance.ID+spec.SolverID+spec.ToolID+fmt.Sprint(time.Now().UnixNano()))))[:25],
	})
	if err != nil {
		return nil, err
	}
	attached, err := r.docker.ContainerAttach(ctx, created.ID, client.ContainerAttachOptions{
		Stream: true, Stdin: true, Stdout: true, Stderr: true,
	})
	if err != nil {
		_, _ = r.docker.ContainerRemove(context.Background(), created.ID, client.ContainerRemoveOptions{Force: true})
		return nil, err
	}
	if _, err := r.docker.ContainerStart(ctx, created.ID, client.ContainerStartOptions{}); err != nil {
		attached.Close()
		_, _ = r.docker.ContainerRemove(context.Background(), created.ID, client.ContainerRemoveOptions{Force: true})
		return nil, err
	}
	process := &Process{
		ID: fmt.Sprintf("p-%s", created.ID[:12]), InstanceID: instanceID,
		FencingToken: fencing, containerID: created.ID,
		attached: attached.HijackedResponse, done: make(chan struct{}),
	}
	r.processes.Store(process.ID, process)
	if spec.Timeout > 0 {
		time.AfterFunc(spec.Timeout, func() {
			_ = r.StopProcess(context.Background(), process.ID, fencing)
		})
	}
	go func() {
		defer close(process.done)
		defer r.processes.Delete(process.ID)
		defer attached.Close()
		defer func() {
			_, _ = r.docker.ContainerRemove(context.Background(), created.ID, client.ContainerRemoveOptions{Force: true, RemoveVolumes: true})
		}()
		var sequence atomic.Uint64
		var total atomic.Int64
		var truncated atomic.Bool
		writer := func(stderr bool) io.Writer {
			return &frameWriter{write: func(data []byte) error {
				remaining := spec.MaxOutputBytes - total.Load()
				if remaining <= 0 {
					truncated.Store(true)
					return nil
				}
				if int64(len(data)) > remaining {
					data = data[:remaining]
					truncated.Store(true)
				}
				total.Add(int64(len(data)))
				return emit(Frame{Sequence: sequence.Add(1), Timestamp: time.Now(), Stderr: stderr, Data: append([]byte(nil), data...)})
			}}
		}
		_, process.err = stdcopy.StdCopy(writer(false), writer(true), attached.Reader)
		inspected, inspectErr := r.docker.ContainerInspect(context.Background(), created.ID, client.ContainerInspectOptions{})
		if process.err == nil {
			process.err = inspectErr
		}
		if inspectErr == nil && inspected.Container.State != nil {
			code := int32(inspected.Container.State.ExitCode)
			process.result.ExitCode = &code
		}
		process.result.Truncated = truncated.Load()
	}()
	return process, nil
}

func (p *Process) Send(data []byte) error {
	_, err := p.attached.Conn.Write(data)
	return err
}

func (p *Process) CloseStdin() error { return p.attached.CloseWrite() }

func (p *Process) Wait(ctx context.Context) (Result, error) {
	select {
	case <-ctx.Done():
		return Result{}, ctx.Err()
	case <-p.done:
		return p.result, p.err
	}
}

func (r *Runtime) StopProcess(ctx context.Context, processID string, fencing uint64) error {
	value, ok := r.processes.Load(processID)
	if !ok {
		return nil
	}
	process := value.(*Process)
	if process.FencingToken != fencing {
		return errors.New("stale fencing token")
	}
	_ = process.CloseStdin()
	process.attached.Close()
	select {
	case <-process.done:
		return nil
	case <-time.After(2 * time.Second):
		target := process.InstanceID
		if process.containerID != "" {
			target = process.containerID
		}
		_, err := r.docker.ContainerKill(ctx, target, client.ContainerKillOptions{Signal: "KILL"})
		return err
	}
}

func (r *Runtime) Inspect(ctx context.Context, instanceID string, fencing uint64) (Instance, int, error) {
	if err := r.validate(ctx, instanceID, fencing); err != nil {
		return Instance{}, 0, err
	}
	info, err := r.docker.ContainerInspect(ctx, instanceID, client.ContainerInspectOptions{})
	if err != nil {
		return Instance{}, 0, err
	}
	return instanceFromLabels(info.Container.ID, info.Container.Config.Labels, info.Container.Created), 0, nil
}

func (r *Runtime) Destroy(ctx context.Context, instanceID string, fencing uint64) error {
	if err := r.validate(ctx, instanceID, fencing); err != nil {
		if strings.Contains(strings.ToLower(err.Error()), "not found") {
			return nil
		}
		return err
	}
	instance, _, err := r.Inspect(ctx, instanceID, fencing)
	if err != nil {
		return err
	}
	if failures := r.removeTaskTools(ctx, instance.TaskID); len(failures) > 0 {
		return failures[0]
	}
	if _, err := r.docker.ContainerRemove(ctx, instanceID, client.ContainerRemoveOptions{Force: true, RemoveVolumes: true}); err != nil {
		return err
	}
	name, _ := taskNetwork(instance.TaskID)
	_, err = r.docker.NetworkRemove(ctx, name, client.NetworkRemoveOptions{})
	return err
}

func (r *Runtime) Reconcile(ctx context.Context, valid map[string]struct{}, before time.Time) ([]Instance, []error) {
	args := client.Filters{}.Add("label", LabelManaged+"=true")
	listed, err := r.docker.ContainerList(ctx, client.ContainerListOptions{All: true, Filters: args})
	if err != nil {
		return nil, []error{err}
	}
	var destroyed []Instance
	var failures []error
	for _, value := range listed.Items {
		kind := value.Labels[LabelKind]
		if kind == "tool" {
			if value.Created < before.Unix() {
				if _, err := r.docker.ContainerRemove(ctx, value.ID, client.ContainerRemoveOptions{Force: true, RemoveVolumes: true}); err != nil {
					failures = append(failures, err)
				}
			}
			continue
		}
		if kind != "sandbox" {
			continue
		}
		if _, ok := valid[value.ID]; ok || value.Created >= before.Unix() {
			continue
		}
		instance := instanceFromLabels(
			value.ID,
			value.Labels,
			time.Unix(value.Created, 0).UTC().Format(time.RFC3339Nano),
		)
		if _, err := r.docker.ContainerRemove(ctx, value.ID, client.ContainerRemoveOptions{Force: true, RemoveVolumes: true}); err != nil {
			failures = append(failures, err)
		} else {
			networkName, _ := taskNetwork(instance.TaskID)
			if _, err := r.docker.NetworkRemove(ctx, networkName, client.NetworkRemoveOptions{}); err != nil {
				failures = append(failures, err)
			}
			destroyed = append(destroyed, instance)
		}
	}
	return destroyed, failures
}

func (r *Runtime) Health(ctx context.Context) (HealthInfo, error) {
	ping, err := r.docker.Ping(ctx, client.PingOptions{})
	if err != nil {
		return HealthInfo{}, err
	}
	info, err := r.docker.Info(ctx, client.InfoOptions{})
	if err != nil {
		return HealthInfo{}, err
	}
	_, runsc := info.Info.Runtimes["runsc"]
	return HealthInfo{
		DockerAPIVersion:       ping.APIVersion,
		RunscRuntimeRegistered: runsc,
	}, nil
}

func (r *Runtime) byTask(ctx context.Context, taskID string) (Instance, error) {
	args := client.Filters{}.
		Add("label", LabelManaged+"=true").
		Add("label", LabelTask+"="+taskID).
		Add("label", LabelKind+"=sandbox")
	listed, err := r.docker.ContainerList(ctx, client.ContainerListOptions{All: true, Filters: args})
	if err != nil || len(listed.Items) == 0 {
		return Instance{}, err
	}
	if len(listed.Items) > 1 {
		return Instance{}, errors.New("multiple active sandboxes for task")
	}
	value := listed.Items[0]
	return instanceFromLabels(value.ID, value.Labels, time.Unix(value.Created, 0).UTC().Format(time.RFC3339Nano)), nil
}

func (r *Runtime) validate(ctx context.Context, id string, fencing uint64) error {
	info, err := r.docker.ContainerInspect(ctx, id, client.ContainerInspectOptions{})
	if err != nil {
		return err
	}
	instance := instanceFromLabels(info.Container.ID, info.Container.Config.Labels, info.Container.Created)
	if instance.TaskID == "" || instance.ConfigDigest != r.config.Digest ||
		info.Container.Config.Labels[LabelKind] != "sandbox" {
		return errors.New("container is not managed by this configuration")
	}
	if fencing != instance.FencingToken {
		return errors.New("stale fencing token")
	}
	return nil
}

func (r *Runtime) removeTaskTools(ctx context.Context, taskID string) []error {
	args := client.Filters{}.
		Add("label", LabelManaged+"=true").
		Add("label", LabelTask+"="+taskID).
		Add("label", LabelKind+"=tool")
	listed, err := r.docker.ContainerList(ctx, client.ContainerListOptions{All: true, Filters: args})
	if err != nil {
		return []error{err}
	}
	var failures []error
	for _, value := range listed.Items {
		if _, err := r.docker.ContainerRemove(ctx, value.ID, client.ContainerRemoveOptions{Force: true, RemoveVolumes: true}); err != nil {
			failures = append(failures, err)
		}
	}
	return failures
}

func labels(task, solver, profile, digest string, fencing uint64) map[string]string {
	return map[string]string{
		LabelManaged: "true", LabelTask: task, LabelSolver: solver,
		LabelProfile: profile, LabelDigest: digest, LabelFencing: fmt.Sprint(fencing),
	}
}

func instanceFromLabels(id string, values map[string]string, created string) Instance {
	var fencing uint64
	_, _ = fmt.Sscan(values[LabelFencing], &fencing)
	return Instance{ID: id, TaskID: values[LabelTask], SolverID: values[LabelSolver],
		ProfileID: values[LabelProfile], ConfigDigest: values[LabelDigest],
		FencingToken: fencing, CreatedAt: created}
}

func validateSpec(spec ProcessSpec) error {
	if (len(spec.Argv) == 0 && spec.ToolID == "") || len(spec.Argv) > 256 {
		return errors.New("invalid argv")
	}
	for _, value := range spec.Argv {
		if strings.ContainsRune(value, 0) {
			return errors.New("argv contains NUL")
		}
	}
	if spec.MaxOutputBytes < 1024 {
		return errors.New("invalid output limit")
	}
	return nil
}

func workspace(logical, solverID string) string {
	switch logical {
	case "task_inputs":
		return "/workspace/inputs"
	case "task_shared":
		return "/workspace/shared"
	default:
		return "/workspace/solvers/" + solverID
	}
}

func (r *Runtime) NetworkPolicyContext(
	ctx context.Context,
	instanceID string,
	fencing uint64,
) (string, []string, error) {
	instance, _, err := r.Inspect(ctx, instanceID, fencing)
	if err != nil {
		return "", nil, err
	}
	networkName, bridge := taskNetwork(instance.TaskID)
	inspected, err := r.docker.NetworkInspect(ctx, networkName, client.NetworkInspectOptions{})
	if err != nil {
		return "", nil, fmt.Errorf("inspect task network: %w", err)
	}
	gateways := make([]string, 0, len(inspected.Network.IPAM.Config))
	for _, ipam := range inspected.Network.IPAM.Config {
		if ipam.Gateway.IsValid() {
			gateways = append(gateways, ipam.Gateway.String())
		}
	}
	if len(gateways) == 0 {
		return "", nil, errors.New("task network has no inspectable gateway")
	}
	return bridge, gateways, nil
}

func taskNetwork(taskID string) (string, string) {
	sum := sha256.Sum256([]byte(taskID))
	suffix := fmt.Sprintf("%x", sum[:6])
	return "tga-net-" + suffix, "tga" + suffix
}

type frameWriter struct{ write func([]byte) error }

func (w *frameWriter) Write(value []byte) (int, error) {
	if err := w.write(value); err != nil {
		return 0, err
	}
	return len(value), nil
}
