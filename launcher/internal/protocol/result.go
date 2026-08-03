// Package protocol defines the JSON contract between the user-facing launcher
// and the internal runtime worker.
//
// The launcher never parses human-readable output: on Windows the worker runs
// inside WSL2 and its stdout crosses a process and OS boundary, so the only
// thing that survives reliably is structured data. Every field here mirrors
// what tga/deployment emits.
package protocol

import "encoding/json"

// Error is an actionable failure carrying a stable code from
// tga/deployment/errors.py.
type Error struct {
	Code        string `json:"code"`
	Detail      string `json:"detail"`
	Remediation string `json:"remediation"`
}

// Error lets a coded failure travel as an ordinary Go error while keeping its
// code and remediation intact for the top-level renderer.
func (e *Error) Error() string {
	if e.Detail == "" {
		return "[" + e.Code + "]"
	}
	return "[" + e.Code + "] " + e.Detail
}

// Step is one stage of the up sequence.
type Step struct {
	Name    string `json:"name"`
	OK      bool   `json:"ok"`
	Skipped bool   `json:"skipped"`
	Detail  string `json:"detail"`
	Code    string `json:"code,omitempty"`
}

// Check is one diagnosed capability.
type Check struct {
	Name   string `json:"name"`
	Status string `json:"status"`
	Detail string `json:"detail,omitempty"`
	Code   string `json:"code,omitempty"`
}

// Hint pairs an error code with its remediation text.
type Hint struct {
	Code string `json:"code"`
	Hint string `json:"hint"`
}

// Result is the union of every worker response. Commands populate the subset
// they need; absent fields stay zero.
type Result struct {
	OK     bool   `json:"ok"`
	Status string `json:"status"`
	URL    string `json:"url"`
	Error  *Error `json:"error,omitempty"`

	// up
	Steps []Step `json:"steps,omitempty"`

	// status
	Platform        string `json:"platform,omitempty"`
	Phase           string `json:"phase,omitempty"`
	Running         bool   `json:"running,omitempty"`
	APIPid          *int   `json:"api_pid,omitempty"`
	LastErrorCode   string `json:"last_error_code,omitempty"`
	LastErrorDetail string `json:"last_error_detail,omitempty"`

	// doctor
	Checks      []Check `json:"checks,omitempty"`
	Remediation []Hint  `json:"remediation,omitempty"`

	// logs
	Component string   `json:"component,omitempty"`
	Path      string   `json:"path,omitempty"`
	Lines     []string `json:"lines,omitempty"`

	// readiness is passed through opaquely; the launcher only reports it.
	Readiness json.RawMessage `json:"readiness,omitempty"`
}

// Parse decodes a worker response.
func Parse(raw []byte) (*Result, error) {
	result := &Result{}
	if err := json.Unmarshal(raw, result); err != nil {
		return nil, err
	}
	return result, nil
}
