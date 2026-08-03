package runtime

import (
	"archive/tar"
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
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
	LabelRun     = "tga.sandbox.solver-run"
	LabelProfile = "tga.sandbox.profile"
	LabelDigest  = "tga.sandbox.config"
	LabelFencing = "tga.sandbox.fencing"
	LabelKind    = "tga.sandbox.kind"
)

type Instance struct {
	ID           string
	TaskID       string
	SolverID     string
	SolverRunID  string
	ProfileID    string
	ConfigDigest string
	FencingToken uint64
	CreatedAt    string
}

type ProcessSpec struct {
	Argv             []string
	Environment      map[string]string
	LogicalWorkspace string
	WorkingDirectory string
	Stdin            []byte
	Interactive      bool
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
	taskID, solverID, solverRunID, profileID, digest string,
	fencing uint64,
) (Instance, bool, error) {
	if !config.ValidIdentifier(taskID) || !config.ValidIdentifier(solverID) || !config.ValidIdentifier(solverRunID) {
		return Instance{}, false, errors.New("invalid task, solver, or SolverRun id")
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
	existing, err := r.bySolverRun(ctx, taskID, solverID, solverRunID)
	if err != nil {
		return Instance{}, false, err
	}
	if existing.ID != "" {
		if existing.ConfigDigest != digest || existing.ProfileID != profileID {
			return Instance{}, false, errors.New("active SolverRun sandbox conflicts with request")
		}
		if fencing < existing.FencingToken {
			return Instance{}, false, errors.New("stale fencing token")
		}
		if err := r.writeFencingToken(taskID, solverRunID, fencing); err != nil {
			return Instance{}, false, fmt.Errorf("persist fencing token: %w", err)
		}
		existing.FencingToken = fencing
		return existing, true, nil
	}
	workspace, err := r.config.Workspace(taskID)
	if err != nil {
		return Instance{}, false, err
	}
	runWorkspace := filepath.Join(workspace, "solver-runs", solverRunID)
	for _, path := range []string{workspace, filepath.Join(workspace, "solver-runs")} {
		if err := os.MkdirAll(path, 0o750); err != nil {
			return Instance{}, false, err
		}
	}
	for _, path := range []string{filepath.Join(workspace, "inputs"), filepath.Join(workspace, "shared")} {
		if err := os.MkdirAll(path, 0o755); err != nil {
			return Instance{}, false, err
		}
		if err := os.Chmod(path, 0o755); err != nil {
			return Instance{}, false, err
		}
	}
	if err := os.Chmod(filepath.Join(workspace, "solver-runs"), 0o711); err != nil {
		return Instance{}, false, err
	}
	if err := os.MkdirAll(runWorkspace, 0o750); err != nil {
		return Instance{}, false, err
	}
	if err := prepareRunWorkspace(runWorkspace); err != nil {
		return Instance{}, false, err
	}
	labels := labels(taskID, solverID, solverRunID, profileID, digest, fencing)
	labels[LabelKind] = "sandbox"
	networkName, bridgeName := runNetwork(taskID, solverRunID)
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
	if profile.AllowPtrace {
		caps = append(caps, "SYS_PTRACE")
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
			Mounts: []mount.Mount{
				{Type: mount.TypeBind, Source: runWorkspace, Target: "/workspace/solver"},
				{Type: mount.TypeBind, Source: filepath.Join(workspace, "inputs"), Target: "/workspace/inputs", ReadOnly: true},
				{Type: mount.TypeBind, Source: filepath.Join(workspace, "shared"), Target: "/workspace/shared", ReadOnly: true},
			},
			Tmpfs: map[string]string{"/tmp": "rw,noexec,nosuid,size=67108864,mode=1777"},
		},
		NetworkingConfig: &network.NetworkingConfig{EndpointsConfig: map[string]*network.EndpointSettings{
			networkName: {},
		}},
		Name: containerName(taskID, solverRunID),
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
	if err := r.verifyToolset(ctx, created.ID, profile); err != nil {
		_, _ = r.docker.ContainerRemove(context.Background(), created.ID, client.ContainerRemoveOptions{Force: true})
		_, _ = r.docker.NetworkRemove(context.Background(), networkCreated.ID, client.NetworkRemoveOptions{})
		return Instance{}, false, err
	}
	if err := r.writeFencingToken(taskID, solverRunID, fencing); err != nil {
		_, _ = r.docker.ContainerRemove(context.Background(), created.ID, client.ContainerRemoveOptions{Force: true})
		_, _ = r.docker.NetworkRemove(context.Background(), networkCreated.ID, client.NetworkRemoveOptions{})
		return Instance{}, false, fmt.Errorf("persist fencing token: %w", err)
	}
	return Instance{
		ID: created.ID, TaskID: taskID, SolverID: solverID, SolverRunID: solverRunID, ProfileID: profileID,
		ConfigDigest: digest, FencingToken: fencing, CreatedAt: time.Now().UTC().Format(time.RFC3339Nano),
	}, false, nil
}

type toolsetManifest struct {
	ProfileID string            `json:"profile_id"`
	Tools     map[string]string `json:"tools"`
}

func (r *Runtime) verifyToolset(ctx context.Context, instanceID string, profile config.Profile) error {
	result, err := r.docker.CopyFromContainer(ctx, instanceID, client.CopyFromContainerOptions{
		SourcePath: "/opt/tga/toolset.json",
	})
	if err != nil {
		return fmt.Errorf("read image toolset: %w", err)
	}
	defer result.Content.Close()
	reader := tar.NewReader(result.Content)
	if _, err := reader.Next(); err != nil {
		return fmt.Errorf("read image toolset archive: %w", err)
	}
	raw, err := io.ReadAll(io.LimitReader(reader, 1<<20))
	if err != nil {
		return fmt.Errorf("read image toolset content: %w", err)
	}
	if err := validateToolset(raw, profile); err != nil {
		return err
	}
	return r.verifyExecutables(ctx, instanceID, profile.AllowedExecutables)
}

func (r *Runtime) verifyExecutables(ctx context.Context, instanceID string, executables []string) error {
	payload, err := json.Marshal(executables)
	if err != nil {
		return err
	}
	const script = `import json, shutil, sys
missing = [name for name in json.loads(sys.argv[1]) if shutil.which(name) is None]
if missing:
    print("missing executables: " + ", ".join(missing), file=sys.stderr)
    raise SystemExit(1)`
	response, err := r.docker.ExecCreate(ctx, instanceID, client.ExecCreateOptions{
		Cmd:          []string{"/usr/bin/python3", "-I", "-c", script, string(payload)},
		AttachStdout: true,
		AttachStderr: true,
	})
	if err != nil {
		return fmt.Errorf("create image executable verification: %w", err)
	}
	attached, err := r.docker.ExecAttach(ctx, response.ID, client.ExecAttachOptions{})
	if err != nil {
		return fmt.Errorf("attach image executable verification: %w", err)
	}
	defer attached.Close()
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	if _, err := stdcopy.StdCopy(&stdout, &stderr, attached.Reader); err != nil && !errors.Is(err, io.EOF) {
		return fmt.Errorf("read image executable verification: %w", err)
	}
	inspected, err := r.docker.ExecInspect(ctx, response.ID, client.ExecInspectOptions{})
	if err != nil {
		return fmt.Errorf("inspect image executable verification: %w", err)
	}
	if inspected.ExitCode != 0 {
		detail := strings.TrimSpace(stderr.String())
		if detail == "" {
			detail = strings.TrimSpace(stdout.String())
		}
		return fmt.Errorf("image executable verification failed: %s", detail)
	}
	return nil
}

func validateToolset(raw []byte, profile config.Profile) error {
	sum := sha256.Sum256(raw)
	if hex.EncodeToString(sum[:]) != profile.ToolsetDigest {
		return errors.New("image toolset digest does not match sandbox profile")
	}
	var manifest toolsetManifest
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&manifest); err != nil {
		return fmt.Errorf("decode image toolset: %w", err)
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return errors.New("image toolset must contain exactly one JSON object")
	}
	if manifest.ProfileID != profile.ID {
		return errors.New("image toolset profile id does not match sandbox profile")
	}
	for _, executable := range profile.AllowedExecutables {
		if _, ok := manifest.Tools[executable]; !ok {
			return fmt.Errorf("image toolset is missing allowed executable %q", executable)
		}
	}
	return nil
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
		Cmd: spec.Argv, Env: env, WorkingDir: workingDirectory(spec),
		AttachStdin: len(spec.Stdin) > 0, AttachStdout: true, AttachStderr: true,
	})
	if err != nil {
		return Result{}, err
	}
	attached, err := r.docker.ExecAttach(ctx, response.ID, client.ExecAttachOptions{})
	if err != nil {
		return Result{}, err
	}
	defer attached.Close()
	if len(spec.Stdin) > 0 {
		if _, err := attached.Conn.Write(spec.Stdin); err != nil {
			return Result{}, err
		}
		if err := attached.CloseWrite(); err != nil {
			return Result{}, err
		}
	}
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
	env := make([]string, 0, len(spec.Environment))
	for key, value := range spec.Environment {
		env = append(env, key+"="+value)
	}
	created, err := r.docker.ExecCreate(ctx, instanceID, client.ExecCreateOptions{
		Cmd: spec.Argv, Env: env, WorkingDir: workingDirectory(spec),
		AttachStdin: true, AttachStdout: true, AttachStderr: true, TTY: spec.Interactive,
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
		if spec.Interactive {
			_, process.err = io.Copy(writer(false), attached.Reader)
		} else {
			_, process.err = stdcopy.StdCopy(writer(false), writer(true), attached.Reader)
		}
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

func (p *Process) Send(data []byte) error {
	_, err := p.attached.Conn.Write(data)
	return err
}

func (p *Process) CloseStdin() error { return p.attached.CloseWrite() }

func (r *Runtime) ResizeProcess(ctx context.Context, processID string, fencing uint64, cols, rows uint32) error {
	value, ok := r.processes.Load(processID)
	if !ok {
		return errors.New("process not found")
	}
	process := value.(*Process)
	if process.FencingToken != fencing {
		return errors.New("stale fencing token")
	}
	if cols < 20 || rows < 5 || cols > 1000 || rows > 1000 {
		return errors.New("invalid terminal size")
	}
	_, err := r.docker.ExecResize(ctx, process.execID, client.ExecResizeOptions{Width: uint(cols), Height: uint(rows)})
	return err
}

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
		_, err := r.docker.ContainerKill(ctx, process.InstanceID, client.ContainerKillOptions{Signal: "KILL"})
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
	instance := instanceFromLabels(info.Container.ID, info.Container.Config.Labels, info.Container.Created)
	if current, err := r.readFencingToken(instance.TaskID, instance.SolverRunID); err == nil {
		instance.FencingToken = current
	}
	return instance, 0, nil
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
	if _, err := r.docker.ContainerRemove(ctx, instanceID, client.ContainerRemoveOptions{Force: true, RemoveVolumes: true}); err != nil {
		return err
	}
	if err := r.removeFencingToken(instance.TaskID, instance.SolverRunID); err != nil {
		return err
	}
	name, _ := runNetwork(instance.TaskID, instance.SolverRunID)
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
			if err := r.removeFencingToken(instance.TaskID, instance.SolverRunID); err != nil {
				failures = append(failures, err)
			}
			networkName, _ := runNetwork(instance.TaskID, instance.SolverRunID)
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

func (r *Runtime) bySolverRun(ctx context.Context, taskID, solverID, solverRunID string) (Instance, error) {
	args := client.Filters{}.
		Add("label", LabelManaged+"=true").
		Add("label", LabelTask+"="+taskID).
		Add("label", LabelSolver+"="+solverID).
		Add("label", LabelRun+"="+solverRunID).
		Add("label", LabelKind+"=sandbox")
	listed, err := r.docker.ContainerList(ctx, client.ContainerListOptions{All: true, Filters: args})
	if err != nil || len(listed.Items) == 0 {
		return Instance{}, err
	}
	if len(listed.Items) > 1 {
		return Instance{}, errors.New("multiple active sandboxes for SolverRun")
	}
	value := listed.Items[0]
	instance := instanceFromLabels(value.ID, value.Labels, time.Unix(value.Created, 0).UTC().Format(time.RFC3339Nano))
	if current, err := r.readFencingToken(taskID, solverRunID); err == nil {
		instance.FencingToken = current
	}
	return instance, nil
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
	currentFencing, err := r.readFencingToken(instance.TaskID, instance.SolverRunID)
	if err != nil {
		return fmt.Errorf("read fencing token: %w", err)
	}
	if fencing != currentFencing {
		return errors.New("stale fencing token")
	}
	return nil
}

func (r *Runtime) fencingPath(taskID, solverRunID string) (string, error) {
	workspace, err := r.config.Workspace(taskID)
	if err != nil {
		return "", err
	}
	return filepath.Join(workspace, ".fencing", solverRunID+".token"), nil
}

func (r *Runtime) writeFencingToken(taskID, solverRunID string, fencing uint64) error {
	path, err := r.fencingPath(taskID, solverRunID)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	temporary := path + ".tmp-" + strconv.FormatUint(fencing, 10)
	if err := os.WriteFile(temporary, []byte(strconv.FormatUint(fencing, 10)+"\n"), 0o600); err != nil {
		return err
	}
	if err := os.Rename(temporary, path); err != nil {
		_ = os.Remove(temporary)
		return err
	}
	return nil
}

func prepareRunWorkspace(path string) error {
	if err := os.Chmod(path, 0o750); err != nil {
		return err
	}
	if err := os.Chown(path, 10001, 10001); err != nil {
		return err
	}
	return nil
}

func (r *Runtime) readFencingToken(taskID, solverRunID string) (uint64, error) {
	path, err := r.fencingPath(taskID, solverRunID)
	if err != nil {
		return 0, err
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return 0, err
	}
	value, err := strconv.ParseUint(strings.TrimSpace(string(raw)), 10, 64)
	if err != nil || value == 0 {
		return 0, errors.New("invalid fencing token")
	}
	return value, nil
}

func (r *Runtime) removeFencingToken(taskID, solverRunID string) error {
	path, err := r.fencingPath(taskID, solverRunID)
	if err != nil {
		return err
	}
	if err := os.Remove(path); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}

func labels(task, solver, solverRun, profile, digest string, fencing uint64) map[string]string {
	return map[string]string{
		LabelManaged: "true", LabelTask: task, LabelSolver: solver,
		LabelRun:     solverRun,
		LabelProfile: profile, LabelDigest: digest, LabelFencing: fmt.Sprint(fencing),
	}
}

func instanceFromLabels(id string, values map[string]string, created string) Instance {
	var fencing uint64
	_, _ = fmt.Sscan(values[LabelFencing], &fencing)
	return Instance{ID: id, TaskID: values[LabelTask], SolverID: values[LabelSolver], SolverRunID: values[LabelRun],
		ProfileID: values[LabelProfile], ConfigDigest: values[LabelDigest],
		FencingToken: fencing, CreatedAt: created}
}

func validateSpec(spec ProcessSpec) error {
	if len(spec.Argv) == 0 || len(spec.Argv) > 256 {
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
	if spec.WorkingDirectory == "" || filepath.IsAbs(spec.WorkingDirectory) {
		return errors.New("invalid working directory")
	}
	for _, part := range strings.Split(filepath.ToSlash(spec.WorkingDirectory), "/") {
		if part == ".." || part == "" {
			return errors.New("working directory escapes logical workspace")
		}
	}
	return nil
}

func workingDirectory(spec ProcessSpec) string {
	base := workspace(spec.LogicalWorkspace, spec.SolverID)
	if spec.WorkingDirectory == "." {
		return base
	}
	return base + "/" + filepath.ToSlash(spec.WorkingDirectory)
}

func workspace(logical, solverID string) string {
	switch logical {
	case "task_inputs":
		return "/workspace/inputs"
	case "task_shared":
		return "/workspace/shared"
	default:
		return "/workspace/solver"
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
	networkName, bridge := runNetwork(instance.TaskID, instance.SolverRunID)
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

func runNetwork(taskID, solverRunID string) (string, string) {
	sum := sha256.Sum256([]byte(taskID + "\x00" + solverRunID))
	suffix := fmt.Sprintf("%x", sum[:6])
	return "tga-net-" + suffix, "tga" + suffix
}

func containerName(taskID, solverRunID string) string {
	sum := sha256.Sum256([]byte(taskID + "\x00" + solverRunID))
	return fmt.Sprintf("tga-%x", sum[:12])
}

type frameWriter struct{ write func([]byte) error }

func (w *frameWriter) Write(value []byte) (int, error) {
	if err := w.write(value); err != nil {
		return 0, err
	}
	return len(value), nil
}
