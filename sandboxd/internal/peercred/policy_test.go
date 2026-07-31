package peercred

import "testing"

func TestAllowedUIDIsExact(t *testing.T) {
	values := map[uint32]struct{}{1001: {}}
	if !allowedUID(values, 1001) {
		t.Fatal("configured UID should be allowed")
	}
	if allowedUID(values, 0) || allowedUID(values, 1002) {
		t.Fatal("unconfigured UID should be denied")
	}
}
