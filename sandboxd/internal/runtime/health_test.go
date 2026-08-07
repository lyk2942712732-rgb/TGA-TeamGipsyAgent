package runtime

import (
	"reflect"
	"testing"

	"github.com/moby/moby/api/types/image"
)

func TestLocalImageDigestsUsesManifestDigests(t *testing.T) {
	images := []image.Summary{
		{RepoDigests: []string{
			"ghcr.io/example/kali@sha256:bbbb",
			"ghcr.io/example/kali@sha256:aaaa",
		}},
		{RepoDigests: []string{
			"mirror.example/kali@sha256:bbbb",
			"invalid",
		}},
	}

	got := localImageDigests(images)
	want := []string{"sha256:aaaa", "sha256:bbbb"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("localImageDigests() = %v, want %v", got, want)
	}
}

func TestReadableEmptyImageStoreHasNoDigests(t *testing.T) {
	if got := localImageDigests(nil); len(got) != 0 {
		t.Fatalf("localImageDigests(nil) = %v, want empty", got)
	}
}
