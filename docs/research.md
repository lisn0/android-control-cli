# Related projects and design choices

These projects and APIs informed the design. Android Control uses ADB and
the Python standard library at runtime.

- [Android CLI skill](https://github.com/android/skills/blob/main/devtools/android-cli/SKILL.md):
  inspect semantic elements, their visibility, and focused fields before input;
  verify the result and use visual inspection when accessibility is insufficient.
- [AndroidX UI Automator stability API](https://developer.android.com/reference/androidx/test/uiautomator/AccessibilityNodeInfoExt):
  bounded stability checks motivate waiting for an unchanged tree rather than
  assuming a fixed delay is enough. Animated content may never stabilize.
- [android-use](https://github.com/ReScienceLab/android-use): useful semantic
  snapshots and device commands. Its bundled input binary is not used here;
  [upstream issue 1](https://github.com/ReScienceLab/android-use/issues/1)
  describes a synthetic sensitive-field snapshot reproduction.
- [Midscene Android automation skill](https://github.com/web-infra-dev/midscene-skills/blob/main/skills/android-automation/SKILL.md):
  explicit verification of actions is useful for signup workflows. This project
  remains a small ADB/standard-library tool and does not depend on its runtime.
- [Mobile Harness](https://github.com/droidrun/mobile-harness): broader
  multi-device and cloud capabilities; it has a larger runtime and optional
  device app. We have not adopted it.

Future candidates include device ownership coordination, app discovery with
foreground checks, and platform-specific onboarding adapters. Treat a stale
or missing screen as a stop condition, not permission to retry a submission.
