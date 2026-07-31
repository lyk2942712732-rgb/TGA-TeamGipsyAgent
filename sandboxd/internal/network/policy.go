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
	grants  map[string][]Grant
}

func New(nftPath string) *Policy {
	if nftPath == "" {
		nftPath = "/usr/sbin/nft"
	}
	return &Policy{nftPath: nftPath, grants: make(map[string][]Grant)}
}

func (p *Policy) Available(ctx context.Context) bool {
	return exec.CommandContext(ctx, p.nftPath, "--version").Run() == nil
}

func (p *Policy) Apply(ctx context.Context, taskID, bridge string, gateways []string, grants []Grant) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	merged := mergeGrants(p.grants[taskID], grants)
	rules, err := Render(taskID, bridge, gateways, merged)
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
	if output, err := exec.CommandContext(ctx, p.nftPath, "--check", "--file", name).CombinedOutput(); err != nil {
		return fmt.Errorf("nft check failed: %s: %w", bytes.TrimSpace(output), err)
	}
	if output, err := exec.CommandContext(ctx, p.nftPath, "--file", name).CombinedOutput(); err != nil {
		return fmt.Errorf("nft apply failed: %s: %w", bytes.TrimSpace(output), err)
	}
	p.grants[taskID] = merged
	return nil
}

func (p *Policy) Delete(ctx context.Context, taskID string) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	file, err := os.CreateTemp("", "tga-nft-delete-*.nft")
	if err != nil {
		return err
	}
	name := file.Name()
	defer os.Remove(name)
	if _, err := fmt.Fprintf(file, "destroy table inet %s\n", tableName(taskID)); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	output, err := exec.CommandContext(ctx, p.nftPath, "--file", name).CombinedOutput()
	if err != nil {
		return fmt.Errorf("nft delete failed: %s: %w", bytes.TrimSpace(output), err)
	}
	delete(p.grants, taskID)
	return nil
}

func mergeGrants(existing, additional []Grant) []Grant {
	portsByCIDR := make(map[string]map[uint32]struct{}, len(existing)+len(additional))
	pingByCIDR := make(map[string]bool, len(existing)+len(additional))
	for _, grant := range append(append([]Grant(nil), existing...), additional...) {
		if len(grant.Ports) == 0 {
			pingByCIDR[grant.CIDR] = true
			continue
		}
		ports := portsByCIDR[grant.CIDR]
		if ports == nil {
			ports = make(map[uint32]struct{})
			portsByCIDR[grant.CIDR] = ports
		}
		for _, port := range grant.Ports {
			ports[port] = struct{}{}
		}
	}
	cidrs := make([]string, 0, len(portsByCIDR)+len(pingByCIDR))
	seen := make(map[string]struct{})
	for cidr := range portsByCIDR {
		seen[cidr] = struct{}{}
		cidrs = append(cidrs, cidr)
	}
	for cidr := range pingByCIDR {
		if _, ok := seen[cidr]; !ok {
			cidrs = append(cidrs, cidr)
		}
	}
	sort.Strings(cidrs)
	merged := make([]Grant, 0, len(cidrs)*2)
	for _, cidr := range cidrs {
		if pingByCIDR[cidr] {
			merged = append(merged, Grant{CIDR: cidr})
		}
		if values := portsByCIDR[cidr]; len(values) > 0 {
			ports := make([]uint32, 0, len(values))
			for port := range values {
				ports = append(ports, port)
			}
			sort.Slice(ports, func(i, j int) bool { return ports[i] < ports[j] })
			merged = append(merged, Grant{CIDR: cidr, Ports: ports})
		}
	}
	return merged
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
