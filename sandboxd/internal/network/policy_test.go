package network

import (
	"strings"
	"testing"
)

func TestRenderIsTaskScopedAndDefaultDeny(t *testing.T) {
	rules, err := Render(
		"task-1",
		"tga1234",
		[]string{"172.20.0.1"},
		[]Grant{{CIDR: "203.0.113.0/24", Ports: []uint32{443}}},
	)
	if err != nil {
		t.Fatal(err)
	}
	for _, expected := range []string{
		`iifname "tga1234" drop`,
		"169.254.0.0/16",
		"100.100.100.200/32",
		"172.20.0.1/32",
		"203.0.113.0/24 tcp dport 443 accept",
	} {
		if !strings.Contains(rules, expected) {
			t.Fatalf("missing %q", expected)
		}
	}
}

func TestRenderRejectsNonCanonicalCIDR(t *testing.T) {
	if _, err := Render("task-1", "tga1234", nil, []Grant{{CIDR: "203.0.113.5/24", Ports: []uint32{80}}}); err == nil {
		t.Fatal("expected non-canonical CIDR to fail")
	}
}

func TestRenderRejectsRuleInjection(t *testing.T) {
	if _, err := Render("task-1", "bad\"\nflush ruleset", nil, nil); err == nil {
		t.Fatal("expected bridge injection to fail")
	}
}

func TestRenderRejectsGatewayInjection(t *testing.T) {
	if _, err := Render("task-1", "tga1234", []string{"172.20.0.1\nflush ruleset"}, nil); err == nil {
		t.Fatal("expected gateway injection to fail")
	}
}
