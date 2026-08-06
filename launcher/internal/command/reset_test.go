package command

import (
	"strings"
	"testing"
)

func TestConfirmationRequiresTheWholeWord(t *testing.T) {
	// `y` is what people type when they are not reading. Removing a
	// distribution takes the run root with it, so it asks for more than that.
	for _, answer := range []string{"y\n", "Y\n", "yeah\n", "no\n", "\n", ""} {
		var out strings.Builder
		if confirmed(&out, strings.NewReader(answer), "TGA-Runtime") {
			t.Fatalf("%q must not be accepted as confirmation", answer)
		}
	}
}

func TestConfirmationAcceptsYesInAnyCase(t *testing.T) {
	for _, answer := range []string{"yes\n", "YES\n", "  Yes  \n"} {
		var out strings.Builder
		if !confirmed(&out, strings.NewReader(answer), "TGA-Runtime") {
			t.Fatalf("%q should confirm", answer)
		}
	}
}

func TestConfirmationSaysWhatIsLostBeforeAsking(t *testing.T) {
	var out strings.Builder
	confirmed(&out, strings.NewReader("no\n"), "TGA-Runtime")

	prompt := out.String()
	for _, expected := range []string{"TGA-Runtime", "task data", "/var/lib/tga/runs"} {
		if !strings.Contains(prompt, expected) {
			t.Fatalf("the prompt never mentions %q:\n%s", expected, prompt)
		}
	}
	if strings.Index(prompt, "deletes") > strings.Index(prompt, "Type 'yes'") {
		t.Fatal("the consequence must be stated before the question")
	}
}

func TestNothingIsRemovedWithoutConfirmation(t *testing.T) {
	// Deliberately host-independent. On a CI runner the distribution does not
	// exist; on a developer's Windows box it does, and this must be safe to
	// run there too -- an earlier version of this test asserted on the phrase
	// "was removed", which the cancellation message happens to contain, so it
	// passed on both hosts while checking nothing.
	var out strings.Builder
	if err := ResetRuntime(&out, strings.NewReader(""), Options{}); err != nil {
		t.Fatalf("ResetRuntime: %v", err)
	}
	text := out.String()
	if strings.Contains(text, "import it again") {
		t.Fatalf("a distribution was unregistered on empty input:\n%s", text)
	}
	if !strings.Contains(text, "nothing to remove") && !strings.Contains(text, "Cancelled") {
		t.Fatalf("unexpected output:\n%s", text)
	}
}
