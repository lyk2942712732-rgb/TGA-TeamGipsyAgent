//go:build linux

package peercred

import (
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestListenerAcceptsConfiguredPeerUID(t *testing.T) {
	path := filepath.Join(t.TempDir(), "peer.sock")
	base, err := net.Listen("unix", path)
	if err != nil {
		t.Fatal(err)
	}
	defer base.Close()
	secured, err := New(base, []uint32{uint32(os.Getuid())})
	if err != nil {
		t.Fatal(err)
	}
	accepted := make(chan error, 1)
	go func() {
		connection, err := secured.Accept()
		if err == nil {
			connection.Close()
		}
		accepted <- err
	}()
	client, err := net.DialTimeout("unix", path, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	client.Close()
	select {
	case err := <-accepted:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("configured peer UID was not accepted")
	}
}
