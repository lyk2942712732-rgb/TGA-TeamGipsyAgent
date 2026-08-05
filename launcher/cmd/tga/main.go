// Command tga is the single public entry point for TGA on every platform.
//
// Users type `tga up`. On Linux that runs the internal worker directly; on
// Windows it manages the dedicated TGA-Runtime WSL2 distribution and forwards
// the same command into it. No user ever runs a deployment script, touches
// systemd, or learns which of the two paths applies to them.
package main

import (
	"errors"
	"flag"
	"fmt"
	"os"

	"github.com/team-gipsy/tga/launcher/internal/command"
	"github.com/team-gipsy/tga/launcher/internal/protocol"
	tgaruntime "github.com/team-gipsy/tga/launcher/internal/runtime"
)

// version is stamped by the release workflow with
// -ldflags "-X main.version=<tag>". An unstamped build reports "dev" rather
// than a number it cannot honour: a binary that names a release it was not
// built from is worse than one that admits it is a local build.
var version = "dev"

const usage = `TGA - authorized security analysis and CTF runtime

Usage:
  tga up          Start TGA and open the interface
  tga down        Stop TGA, preserving all task data
  tga status      Show what is currently running
  tga doctor      Diagnose the deployment and print fixes
  tga logs        Show component logs

Flags:
  --port <n>      Port for the interface (default 8123)
  --host <addr>   Bind address (default 127.0.0.1)
  --no-open       Do not open a browser
  --public        Serve for remote access instead of localhost only
  --json          Emit machine-readable JSON
  --component <c> Log component for 'tga logs' (default api)
  --lines <n>     Log lines for 'tga logs' (default 200)
`

func main() {
	if len(os.Args) < 2 {
		fmt.Print(usage)
		os.Exit(2)
	}
	verb := os.Args[1]
	switch verb {
	case "-h", "--help", "help":
		fmt.Print(usage)
		return
	case "version", "--version":
		fmt.Printf("tga launcher %s\n", version)
		return
	}

	opts, err := parseFlags(verb, os.Args[2:])
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(2)
	}

	runner, err := tgaruntime.Resolve()
	if err != nil {
		reportError(err)
		os.Exit(1)
	}

	if err := dispatch(verb, runner, opts); err != nil {
		if errors.Is(err, command.ErrUnknownCommand) {
			fmt.Fprintf(os.Stderr, "error: unknown command %q\n\n", verb)
			fmt.Fprint(os.Stderr, usage)
			os.Exit(2)
		}
		reportError(err)
		os.Exit(1)
	}
}

func parseFlags(verb string, args []string) (command.Options, error) {
	opts := command.Options{
		Host: "127.0.0.1", Port: 8123, Component: "api", Lines: 200, Timeout: 90,
	}
	set := flag.NewFlagSet("tga "+verb, flag.ContinueOnError)
	set.SetOutput(os.Stderr)
	set.StringVar(&opts.Host, "host", opts.Host, "bind address")
	set.IntVar(&opts.Port, "port", opts.Port, "interface port")
	set.BoolVar(&opts.NoOpen, "no-open", false, "do not open a browser")
	set.BoolVar(&opts.Public, "public", false, "serve for remote access")
	set.BoolVar(&opts.JSON, "json", false, "machine-readable output")
	set.StringVar(&opts.Component, "component", opts.Component, "log component")
	set.IntVar(&opts.Lines, "lines", opts.Lines, "log lines")
	set.Float64Var(&opts.Timeout, "timeout", opts.Timeout, "readiness timeout in seconds")
	if err := set.Parse(args); err != nil {
		return opts, err
	}
	// Public mode binds every interface; the operator is expected to put a
	// reverse proxy in front. Never open a local browser for it.
	if opts.Public {
		opts.Host = "0.0.0.0"
		opts.NoOpen = true
	}
	return opts, nil
}

func dispatch(verb string, runner tgaruntime.Runner, opts command.Options) error {
	switch verb {
	case "up":
		return command.Up(os.Stdout, runner, opts)
	case "down":
		return command.Down(os.Stdout, runner, opts)
	case "status":
		return command.Status(os.Stdout, runner, opts)
	case "doctor":
		return command.Doctor(os.Stdout, runner, opts)
	case "logs":
		return command.Logs(os.Stdout, runner, opts)
	default:
		return command.ErrUnknownCommand
	}
}

// reportError prints a coded failure with its remediation, so the user is told
// what to do rather than just what broke.
func reportError(err error) {
	var coded *protocol.Error
	if errors.As(err, &coded) {
		fmt.Fprintf(os.Stderr, "error: [%s] %s\n", coded.Code, coded.Detail)
		if coded.Remediation != "" {
			fmt.Fprintf(os.Stderr, "  -> %s\n", coded.Remediation)
		}
		return
	}
	fmt.Fprintf(os.Stderr, "error: %v\n", err)
}
