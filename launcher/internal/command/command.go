// Package command implements the user-facing verbs.
//
// Rendering lives here and nowhere else: the worker returns data, the launcher
// decides how it looks. Keeping presentation in one place is what lets Windows
// and Linux produce byte-identical output for the same deployment state.
package command

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os/exec"
	"runtime"
	"strconv"
	"strings"

	"github.com/team-gipsy/tga/launcher/internal/protocol"
	tgaruntime "github.com/team-gipsy/tga/launcher/internal/runtime"
)

// Options carries parsed flags shared by the verbs.
type Options struct {
	Host      string
	Port      int
	NoOpen    bool
	Public    bool
	JSON      bool
	Component string
	Lines     int
	Timeout   float64
}

// Up brings the deployment to a serving state and opens the UI.
func Up(out io.Writer, runner tgaruntime.Runner, opts Options) error {
	args := []string{"up", "--host", opts.Host, "--port", strconv.Itoa(opts.Port)}
	if opts.Timeout > 0 {
		args = append(args, "--timeout", strconv.FormatFloat(opts.Timeout, 'f', 0, 64))
	}
	// The worker never opens a browser; on Windows it could not reach one from
	// inside WSL2 anyway. The launcher owns that step.
	args = append(args, "--no-open")

	result, err := tgaruntime.Invoke(runner, args...)
	if err != nil {
		return err
	}
	if opts.JSON {
		return writeJSON(out, result)
	}

	for _, step := range result.Steps {
		fmt.Fprintln(out, renderStep(step))
	}
	if result.Error != nil {
		return result.Error
	}
	fmt.Fprintf(out, "\nTGA is %s at %s\n", result.Status, result.URL)
	if result.Status == "degraded" {
		fmt.Fprintln(out, "Sandbox isolation is not enforced. Run `tga doctor` for details.")
	}
	if !opts.NoOpen && !opts.Public && result.URL != "" {
		openBrowser(result.URL)
	}
	return nil
}

// Down stops the deployment while preserving all data.
func Down(out io.Writer, runner tgaruntime.Runner, opts Options) error {
	result, err := tgaruntime.Invoke(runner, "down")
	if err != nil {
		return err
	}
	if opts.JSON {
		return writeJSON(out, result)
	}
	if result.Error != nil {
		return result.Error
	}
	fmt.Fprintln(out, "TGA stopped. Task data was preserved.")
	return nil
}

// Status reports current deployment state without changing it.
func Status(out io.Writer, runner tgaruntime.Runner, opts Options) error {
	result, err := tgaruntime.Invoke(runner, "status")
	if err != nil {
		return err
	}
	if opts.JSON {
		return writeJSON(out, result)
	}
	url := result.URL
	if url == "" {
		url = "-"
	}
	fmt.Fprintf(out, "%-20s %s\n", "Platform", result.Platform)
	fmt.Fprintf(out, "%-20s %s\n", "Surface", runner.Describe())
	fmt.Fprintf(out, "%-20s %s\n", "Phase", result.Phase)
	fmt.Fprintf(out, "%-20s %t\n", "Running", result.Running)
	fmt.Fprintf(out, "%-20s %s\n", "Frontend", url)
	if result.LastErrorCode != "" {
		fmt.Fprintf(out, "%-20s %s: %s\n", "Last error", result.LastErrorCode, result.LastErrorDetail)
	}
	return nil
}

// Doctor diagnoses every capability and prints remediation for failures.
func Doctor(out io.Writer, runner tgaruntime.Runner, opts Options) error {
	result, err := tgaruntime.Invoke(runner, "doctor")
	if err != nil {
		return err
	}
	if opts.JSON {
		return writeJSON(out, result)
	}
	for _, check := range result.Checks {
		line := fmt.Sprintf("%s %-24s", mark(check.Status), check.Name)
		if check.Detail != "" {
			line += " " + check.Detail
		}
		if check.Code != "" {
			line += "  [" + check.Code + "]"
		}
		fmt.Fprintln(out, strings.TrimRight(line, " "))
	}
	for _, hint := range result.Remediation {
		fmt.Fprintf(out, "\n[%s]\n  %s\n", hint.Code, hint.Hint)
	}
	fmt.Fprintf(out, "\nstatus: %s\n", result.Status)
	return nil
}

// Logs prints a component log tail.
func Logs(out io.Writer, runner tgaruntime.Runner, opts Options) error {
	result, err := tgaruntime.Invoke(runner,
		"logs", "--component", opts.Component, "--lines", strconv.Itoa(opts.Lines))
	if err != nil {
		return err
	}
	if opts.JSON {
		return writeJSON(out, result)
	}
	if !result.OK {
		return fmt.Errorf("no log for component %q at %s", opts.Component, result.Path)
	}
	for _, line := range result.Lines {
		fmt.Fprintln(out, line)
	}
	return nil
}

func renderStep(step protocol.Step) string {
	symbol := "[!!]"
	switch {
	case step.Skipped:
		symbol = "[--]"
	case step.OK:
		symbol = "[OK]"
	}
	line := fmt.Sprintf("%s %-26s", symbol, step.Name)
	if step.Detail != "" {
		line += " " + truncate(step.Detail, 90)
	}
	if step.Code != "" {
		line += "  [" + step.Code + "]"
	}
	return strings.TrimRight(line, " ")
}

func mark(status string) string {
	switch status {
	case "ready":
		return "[OK]"
	case "disabled":
		return "[--]"
	case "unknown":
		return "[??]"
	default:
		return "[!!]"
	}
}

func truncate(value string, limit int) string {
	if len(value) <= limit {
		return value
	}
	return value[:limit-3] + "..."
}

func writeJSON(out io.Writer, result *protocol.Result) error {
	encoded, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return err
	}
	_, err = out.Write(append(encoded, '\n'))
	return err
}

// openBrowser is best effort: a server install has no browser, and failing to
// open one must never turn a successful startup into a failure.
func openBrowser(url string) {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		cmd = exec.Command("rundll32", "url.dll,FileProtocolHandler", url)
	case "darwin":
		cmd = exec.Command("open", url)
	default:
		if _, err := exec.LookPath("xdg-open"); err != nil {
			return
		}
		cmd = exec.Command("xdg-open", url)
	}
	_ = cmd.Start()
}

// ErrUnknownCommand is returned for an unrecognised verb.
var ErrUnknownCommand = errors.New("unknown command")
