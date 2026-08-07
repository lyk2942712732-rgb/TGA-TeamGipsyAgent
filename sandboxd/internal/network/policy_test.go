package network

import (
	"context"
	"errors"
	"os"
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

func TestManagedTableNamesOnlyReturnsOwnedInetTables(t *testing.T) {
	got := managedTableNames(strings.Join([]string{
		"table inet filter",
		"table ip tga_123456789abc",
		"table inet tga_ABCDEF123456",
		"table inet tga_123456789abc",
		"table inet tga_000000000000",
		"table inet tga_123456789abc",
	}, "\n"))
	if strings.Join(got, ",") != "tga_000000000000,tga_123456789abc" {
		t.Fatalf("unexpected managed tables: %v", got)
	}
}

func TestReconcileDeletesOnlyStaleManagedTables(t *testing.T) {
	policy := New("nft")
	validID := "container-valid"
	validTable := tableName(validID)
	staleTable := tableName("container-stale")
	var deleted []string
	policy.run = func(_ context.Context, args ...string) ([]byte, error) {
		if strings.Join(args, " ") == "list tables" {
			return []byte("table inet filter\ntable inet " + validTable + "\ntable inet " + staleTable + "\n"), nil
		}
		if len(args) == 2 && args[0] == "--file" {
			content, err := os.ReadFile(args[1])
			if err != nil {
				return nil, err
			}
			deleted = append(deleted, strings.TrimSpace(string(content)))
			return nil, nil
		}
		return nil, errors.New("unexpected nft invocation")
	}
	removed, err := policy.Reconcile(context.Background(), []string{validID})
	if err != nil {
		t.Fatal(err)
	}
	if len(removed) != 1 || removed[0] != staleTable {
		t.Fatalf("unexpected removed tables: %v", removed)
	}
	if len(deleted) != 1 || deleted[0] != "destroy table inet "+staleTable {
		t.Fatalf("unexpected delete commands: %v", deleted)
	}
}

func TestDeleteIsIdempotentWhenTableIsAlreadyGone(t *testing.T) {
	policy := New("nft")
	policy.run = func(_ context.Context, _ ...string) ([]byte, error) {
		return []byte("Error: No such file or directory"), errors.New("exit status 1")
	}
	if err := policy.Delete(context.Background(), "missing-container"); err != nil {
		t.Fatalf("delete should be idempotent: %v", err)
	}
}
