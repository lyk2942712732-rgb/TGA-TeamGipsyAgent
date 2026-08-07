package network

import (
	"bytes"
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"net/netip"
	"os"
	"os/exec"
	"sort"
	"strings"
	"sync"
)

type Grant struct {
	CIDR  string
	Ports []uint32
}

type Policy struct {
	nftPath string
	mu      sync.Mutex
	run     func(context.Context, ...string) ([]byte, error)
}

func New(nftPath string) *Policy {
	if nftPath == "" {
		nftPath = "/usr/sbin/nft"
	}
	policy := &Policy{nftPath: nftPath}
	policy.run = func(ctx context.Context, args ...string) ([]byte, error) {
		return exec.CommandContext(ctx, policy.nftPath, args...).CombinedOutput()
	}
	return policy
}

func (p *Policy) Available(ctx context.Context) bool {
	_, err := p.run(ctx, "--version")
	return err == nil
}

func (p *Policy) Apply(ctx context.Context, taskID, bridge string, gateways []string, grants []Grant) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	rules, err := Render(taskID, bridge, gateways, grants)
	if err != nil {
		return err
	}
	file, err := os.CreateTemp("", "tga-nft-*.nft")
	if err != nil {
		return err
	}
	name := file.Name()
	defer os.Remove(name)
	if _, err := file.WriteString(rules); err != nil {
		file.Close()
		return err
	}
	if err := file.Chmod(0o600); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	if output, err := p.run(ctx, "--check", "--file", name); err != nil {
		return fmt.Errorf("nft check failed: %s: %w", bytes.TrimSpace(output), err)
	}
	if output, err := p.run(ctx, "--file", name); err != nil {
		return fmt.Errorf("nft apply failed: %s: %w", bytes.TrimSpace(output), err)
	}
	return nil
}

func (p *Policy) Delete(ctx context.Context, taskID string) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.deleteLocked(ctx, taskID)
}

func (p *Policy) deleteLocked(ctx context.Context, taskID string) error {
	return p.deleteTableLocked(ctx, tableName(taskID))
}

func (p *Policy) deleteTableLocked(ctx context.Context, table string) error {
	file, err := os.CreateTemp("", "tga-nft-delete-*.nft")
	if err != nil {
		return err
	}
	name := file.Name()
	defer os.Remove(name)
	if _, err := fmt.Fprintf(file, "destroy table inet %s\n", table); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	output, err := p.run(ctx, "--file", name)
	if err != nil {
		lower := strings.ToLower(string(output) + " " + err.Error())
		if strings.Contains(lower, "no such file") || strings.Contains(lower, "no such table") {
			return nil
		}
		return fmt.Errorf("nft delete failed: %s: %w", bytes.TrimSpace(output), err)
	}
	return nil
}

// Reconcile removes TGA-owned nft tables which do not belong to any retained
// managed container.  It intentionally ignores every non-TGA table.
func (p *Policy) Reconcile(ctx context.Context, validPolicyIDs []string) ([]string, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	output, err := p.run(ctx, "list", "tables")
	if err != nil {
		return nil, fmt.Errorf("nft list tables failed: %s: %w", bytes.TrimSpace(output), err)
	}
	valid := make(map[string]struct{}, len(validPolicyIDs))
	for _, id := range validPolicyIDs {
		valid[tableName(id)] = struct{}{}
	}
	var removed []string
	for _, name := range managedTableNames(string(output)) {
		if _, ok := valid[name]; ok {
			continue
		}
		if err := p.deleteTableLocked(ctx, name); err != nil {
			return removed, err
		}
		removed = append(removed, name)
	}
	return removed, nil
}

func managedTableNames(output string) []string {
	seen := make(map[string]struct{})
	var names []string
	for _, line := range strings.Split(output, "\n") {
		fields := strings.Fields(line)
		if len(fields) != 3 || fields[0] != "table" || fields[1] != "inet" {
			continue
		}
		name := fields[2]
		if len(name) != 16 || !strings.HasPrefix(name, "tga_") || !isLowerHex(name[4:]) {
			continue
		}
		if _, ok := seen[name]; ok {
			continue
		}
		seen[name] = struct{}{}
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

func isLowerHex(value string) bool {
	for _, char := range value {
		if !((char >= '0' && char <= '9') || (char >= 'a' && char <= 'f')) {
			return false
		}
	}
	return value != ""
}

func Render(taskID, bridge string, gateways []string, grants []Grant) (string, error) {
	if bridge == "" || strings.ContainsAny(bridge, "\"\n\r\x00") {
		return "", errors.New("invalid bridge")
	}
	table := tableName(taskID)
	var deny4 = []string{"127.0.0.0/8", "169.254.0.0/16", "100.100.100.200/32"}
	var deny6 = []string{"::1/128", "fe80::/10", "fd00:ec2::254/128"}
	for _, gateway := range gateways {
		address, err := netip.ParseAddr(gateway)
		if err != nil || address.String() != gateway {
			return "", errors.New("gateway must be canonical")
		}
		if address.Is4() {
			deny4 = append(deny4, address.String()+"/32")
		} else {
			deny6 = append(deny6, address.String()+"/128")
		}
	}
	var allow4, allow6 []string
	for _, grant := range grants {
		prefix, err := netip.ParsePrefix(grant.CIDR)
		if err != nil || prefix != prefix.Masked() || prefix.String() != grant.CIDR {
			return "", errors.New("CIDR must be canonical")
		}
		if len(grant.Ports) == 0 {
			family, protocol := "ip", "icmp"
			if prefix.Addr().Is6() {
				family, protocol = "ip6", "ipv6-icmp"
			}
			line := fmt.Sprintf("    iifname %q %s daddr %s meta l4proto %s accept\n", bridge, family, prefix, protocol)
			if family == "ip" {
				allow4 = append(allow4, line)
			} else {
				allow6 = append(allow6, line)
			}
			continue
		}
		ports := append([]uint32(nil), grant.Ports...)
		sort.Slice(ports, func(i, j int) bool { return ports[i] < ports[j] })
		for _, port := range ports {
			if port == 0 || port > 65535 {
				return "", errors.New("invalid port")
			}
			family := "ip"
			if prefix.Addr().Is6() {
				family = "ip6"
			}
			line := fmt.Sprintf("    iifname %q %s daddr %s tcp dport %d accept\n", bridge, family, prefix, port)
			if family == "ip" {
				allow4 = append(allow4, line)
			} else {
				allow6 = append(allow6, line)
			}
		}
	}
	var out strings.Builder
	fmt.Fprintf(&out, "destroy table inet %s\n", table)
	fmt.Fprintf(&out, "table inet %s {\n  chain forward {\n    type filter hook forward priority -5; policy accept;\n", table)
	fmt.Fprintf(&out, "    iifname %q ct state established,related accept\n", bridge)
	// These ranges are denied even when a broad grant accidentally overlaps.
	fmt.Fprintf(&out, "    iifname %q ip daddr { %s } drop\n", bridge, strings.Join(deny4, ", "))
	fmt.Fprintf(&out, "    iifname %q ip6 daddr { %s } drop\n", bridge, strings.Join(deny6, ", "))
	for _, line := range append(allow4, allow6...) {
		out.WriteString(line)
	}
	fmt.Fprintf(&out, "    iifname %q drop\n  }\n}\n", bridge)
	return out.String(), nil
}

func tableName(taskID string) string {
	sum := sha256.Sum256([]byte(taskID))
	return fmt.Sprintf("tga_%x", sum[:6])
}
