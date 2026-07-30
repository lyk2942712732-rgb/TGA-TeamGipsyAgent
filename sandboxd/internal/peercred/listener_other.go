//go:build !linux

package peercred

import (
	"errors"
	"net"
)

func New(base net.Listener, allowed []uint32) (net.Listener, error) {
	_ = base
	_ = allowed
	return nil, errors.New("SO_PEERCRED sandbox listener is Linux-only")
}
