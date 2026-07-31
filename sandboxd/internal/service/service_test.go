package service

import (
	"sync"
	"testing"
	"time"
)

func TestTaskExecutionLockSerializesSameTaskExecutions(t *testing.T) {
	service := &Service{locks: make(map[string]*taskExecutionLock)}
	releaseFirst := service.lockTaskExecution("instance-a", true)
	entered := make(chan struct{})
	go func() {
		releaseSecond := service.lockTaskExecution("instance-a", true)
		close(entered)
		releaseSecond()
	}()

	select {
	case <-entered:
		t.Fatal("same-task execution entered before the prior network grant was released")
	case <-time.After(25 * time.Millisecond):
	}
	releaseFirst()
	select {
	case <-entered:
	case <-time.After(time.Second):
		t.Fatal("same-task execution did not resume")
	}
}

func TestTaskExecutionLockSerializesDestroyAgainstExecution(t *testing.T) {
	service := &Service{locks: make(map[string]*taskExecutionLock)}
	releaseExecution := service.lockTaskExecution("instance-a", false)
	entered := make(chan struct{})
	go func() {
		releaseDestroy := service.lockTaskExecution("instance-a", true)
		close(entered)
		releaseDestroy()
	}()

	select {
	case <-entered:
		t.Fatal("destroy entered while a task execution was active")
	case <-time.After(25 * time.Millisecond):
	}
	releaseExecution()
	select {
	case <-entered:
	case <-time.After(time.Second):
		t.Fatal("destroy did not resume after task execution ended")
	}
}

func TestTaskExecutionLockAllowsDifferentTasks(t *testing.T) {
	service := &Service{locks: make(map[string]*taskExecutionLock)}
	releaseFirst := service.lockTaskExecution("instance-a", false)
	defer releaseFirst()

	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		releaseSecond := service.lockTaskExecution("instance-b", false)
		releaseSecond()
	}()
	done := make(chan struct{})
	go func() { wg.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("different-task execution was incorrectly serialized")
	}
}
