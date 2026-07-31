//go:build linux

package peercred

import (
	"errors"
	"net"

	"golang.org/x/sys/unix"
)

type listener struct {
	net.Listener
	allowed map[uint32]struct{}
}

func New(base net.Listener, allowed []uint32) (net.Listener, error) {
	if len(allowed) == 0 {
		return nil, errors.New("allowed client UID list must not be empty")
	}
	values := make(map[uint32]struct{}, len(allowed))
	for _, uid := range allowed {
		values[uid] = struct{}{}
	}
	return &listener{Listener: base, allowed: values}, nil
}

func (l *listener) Accept() (net.Conn, error) {
	for {
		connection, err := l.Listener.Accept()
		if err != nil {
			return nil, err
		}
		unixConnection, ok := connection.(*net.UnixConn)
		if !ok {
			connection.Close()
			continue
		}
		raw, err := unixConnection.SyscallConn()
		if err != nil {
			connection.Close()
			continue
		}
		var credentials *unix.Ucred
		var controlErr error
		if err := raw.Control(func(fd uintptr) {
			credentials, controlErr = unix.GetsockoptUcred(int(fd), unix.SOL_SOCKET, unix.SO_PEERCRED)
		}); err != nil || controlErr != nil || credentials == nil {
			connection.Close()
			continue
		}
		if !allowedUID(l.allowed, credentials.Uid) {
			connection.Close()
			continue
		}
		return connection, nil
	}
}
