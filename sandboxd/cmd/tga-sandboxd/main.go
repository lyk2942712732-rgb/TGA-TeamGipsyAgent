package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net"
	"os"
	"os/signal"
	"os/user"
	"strconv"
	"syscall"
	"time"

	"google.golang.org/grpc"

	sandboxv1 "github.com/team-gipsy/tga-sandboxd/api/sandbox/v1"
	"github.com/team-gipsy/tga-sandboxd/internal/config"
	"github.com/team-gipsy/tga-sandboxd/internal/network"
	"github.com/team-gipsy/tga-sandboxd/internal/peercred"
	runtimepkg "github.com/team-gipsy/tga-sandboxd/internal/runtime"
	"github.com/team-gipsy/tga-sandboxd/internal/service"
)

func main() {
	var configPath string
	flag.StringVar(&configPath, "config", "/etc/tga/sandbox.json", "root-owned sandbox configuration")
	flag.Parse()
	if os.Geteuid() != 0 {
		log.Fatal("tga-sandboxd must run as root")
	}
	cfg, err := config.Load(configPath)
	if err != nil {
		log.Fatal(err)
	}
	if err := prepareSocket(cfg.Sandboxd.SocketPath); err != nil {
		log.Fatal(err)
	}
	listener, err := net.Listen("unix", cfg.Sandboxd.SocketPath)
	if err != nil {
		log.Fatal(err)
	}
	defer listener.Close()
	if err := secureSocket(cfg.Sandboxd.SocketPath); err != nil {
		log.Fatal(err)
	}
	listener, err = peercred.New(listener, cfg.Sandboxd.AllowedClientUIDs)
	if err != nil {
		log.Fatal(err)
	}
	engine, err := runtimepkg.New(cfg)
	if err != nil {
		log.Fatal(err)
	}
	defer engine.Close()
	server := grpc.NewServer(
		grpc.MaxRecvMsgSize(4*1024*1024),
		grpc.MaxSendMsgSize(4*1024*1024),
	)
	sandboxv1.RegisterSandboxServiceServer(server, service.New(cfg, engine, network.New("")))
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	go func() {
		<-ctx.Done()
		done := make(chan struct{})
		go func() { server.GracefulStop(); close(done) }()
		select {
		case <-done:
		case <-time.After(10 * time.Second):
			server.Stop()
		}
	}()
	log.Printf("tga-sandboxd listening on %s", cfg.Sandboxd.SocketPath)
	if err := server.Serve(listener); err != nil {
		log.Fatal(err)
	}
}

func prepareSocket(path string) error {
	if err := os.MkdirAll(filepathDir(path), 0o750); err != nil {
		return err
	}
	if info, err := os.Lstat(path); err == nil {
		if info.Mode()&os.ModeSocket == 0 {
			return fmt.Errorf("refusing to replace non-socket %s", path)
		}
		return os.Remove(path)
	} else if !os.IsNotExist(err) {
		return err
	}
	return nil
}

func secureSocket(path string) error {
	group, err := user.LookupGroup("tga-sandbox")
	if err != nil {
		return err
	}
	gid, err := strconv.Atoi(group.Gid)
	if err != nil {
		return err
	}
	if err := os.Chown(path, 0, gid); err != nil {
		return err
	}
	return os.Chmod(path, 0o660)
}

func filepathDir(path string) string {
	for index := len(path) - 1; index >= 0; index-- {
		if path[index] == '/' {
			if index == 0 {
				return "/"
			}
			return path[:index]
		}
	}
	return "."
}
