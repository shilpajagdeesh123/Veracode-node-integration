---
name: veracode-agent
description: Starts the Veracode Enterprise API scan from the IDE integrated terminal. Use when delegated by the main skill through Veracode-S.
---

# Veracode Agent Skill

## Purpose

This skill is the execution handoff for the Veracode Enterprise scanner.

The actual scan must run in the IDE integrated terminal through the scripts under `veracode-enterprise`.

Copilot must not perform the Veracode scan itself.

## Invocation

This skill may be invoked by the main skill when the user enters:

`Veracode-S`

It may also receive an optional project/module name, for example:

`Veracode-S customer-service`

Treat any value passed after `Veracode-S` as the requested scan target. Do not independently scan or inspect that target with Copilot.

## Mandatory Fresh-Start Rule

Every invocation is a new execution.

Do not:

- resume a previous Veracode agent run;
- reuse previous terminal output;
- continue a previous scan conversation;
- assume a previous scan completed;
- read an old `results.json` before starting;
- use previous project/module selections as the current selection.

Always execute the launcher again.

Persistent configuration such as valid `veracode-secrets.json` may be reused by the terminal workflow. Existing historical output must not be deleted.

## Repository Root

Determine the workspace/repository root that contains:

`veracode-enterprise`

and:

`.GitHub`

Run the launcher from that repository root.

Do not recursively inspect application source code to determine the root.

## Operating-System Detection

Detect only the host operating system needed to choose the launcher.

### Windows

Run the following command in a NEW IDE integrated terminal:

`cmd.exe /d /c "veracode-enterprise\runtime-veracode.bat"`

Do not use PowerShell `call`.

### macOS

Run the following command in a NEW IDE integrated terminal:

`bash veracode-enterprise/runtime-veracode.sh`

### Unsupported operating system

If the operating system is neither Windows nor macOS, stop and tell the user that the current Veracode Enterprise launcher supports Windows and macOS only.

## Terminal Handoff Is Mandatory

The launcher must run in the IDE integrated terminal so that the user can interact with:

- runtime setup;
- HMAC credential prompts;
- project/module selection;
- scan mode selection;
- Veracode API execution.

Do not execute the scan internally in Copilot chat.

Do not replace the terminal workflow with source-code analysis.

## Stop Immediately After Launch

After the terminal command has been successfully submitted, this skill is complete.

STOP execution immediately.

Do not:

- wait for `runtime-veracode.bat` or `runtime-veracode.sh` to finish;
- poll terminal output;
- repeatedly read terminal output;
- wait for scan status;
- wait for PENDING, STARTED, or SUCCESS;
- read `results.json`;
- read generated CSV files;
- inspect `.GitHub/output`;
- count findings;
- interpret severity;
- start remediation;
- modify source code;
- generate patches;
- run another command after the launcher;
- provide a Veracode scan result in Copilot.

The terminal owns the workflow after handoff.

A successful terminal handoff is the end of this skill.

## Runtime and Credential Responsibility

Do not perform Python/runtime installation or HMAC credential handling in Copilot.

The terminal launcher is responsible for:

1. checking whether the required local Python runtime exists;
2. running `setup-runtime.bat` on Windows when required;
3. running the corresponding macOS runtime setup when required;
4. checking `veracode-secrets.json`;
5. prompting for the HMAC API ID when credentials are missing or incomplete;
6. prompting for the HMAC API key with hidden input;
7. using `api.veracode.com` as the default API host;
8. saving the local credential configuration;
9. continuing with the Veracode API workflow.

Never request HMAC credentials in Copilot chat.

Never print, copy, summarize, expose, or commit HMAC credentials.

## Scan Target Handling

If no project/module was supplied with `Veracode-S`, launch the terminal workflow and allow the scanner to display its project/module selection menu.

If a project/module was supplied, pass it to the terminal launcher only if the current launcher explicitly supports a target argument.

Do not invent a command-line argument.

If the launcher does not support a target argument, launch it normally and let the user select the target in the terminal.

## Scanner-Owned Module Discovery

Copilot must not construct its own module list.

The scanner is responsible for discovering eligible modules/projects.

Scanner discovery must exclude infrastructure and generated directories such as:

- `.GitHub`
- `.github`
- `veracode-enterprise`
- `.git`
- `.idea`
- `.vscode`
- `node_modules`
- `target`
- `dist`
- `build`
- `out`
- `coverage`

## Expected Scan Output

The terminal scanner, not this skill, writes scan results under:

`.GitHub/output/veracode/<project-or-module>/<date>/`

The skill must not create this directory itself.

## Mainframe

The project/module name must remain the actual project/module name.

Do not treat `src` as the project/module name.

Do not generate:

`.GitHub/output/veracode/src/src/<date>/`

The scanner owns mainframe packaging and output-path resolution.

## Complete Findings Retrieval

The terminal scanner is responsible for retrieving all findings available from the selected Veracode API workflow, including required pagination.

This skill must not attempt to compensate for slow API execution by reading partial results.

Do not terminate the terminal scan because retrieval is taking time.

## Separation from Remediation

This skill performs scan-launch handoff only.

It must never invoke the remediation skill automatically.

Remediation is a separate user-requested workflow and operates on completed scan output under:

`.GitHub/output/veracode/`

Expected remediation output is:

`.GitHub/remediation/veracode/<project-or-module>/<scan-date>/`

## Required Behavior Summary

For every invocation:

1. Receive the `Veracode-S` delegation.
2. Treat the invocation as a fresh run.
3. Locate the repository root without scanning application source.
4. Detect Windows or macOS.
5. Open/use a NEW IDE integrated terminal.
6. Windows: submit `cmd.exe /d /c "veracode-enterprise\runtime-veracode.bat"`.
7. macOS: submit `bash veracode-enterprise/runtime-veracode.sh`.
8. Confirm only that the command was submitted successfully.
9. STOP immediately.
10. Leave all remaining interaction and Veracode processing to the terminal.

## Prohibited Copilot Behavior

Do not perform additional repository analysis before terminal handoff.

Do not say that Copilot itself is scanning the project.

Do not keep the agent active while the scan runs.

Do not automatically remediate after scanning.

Do not expose secrets.

Do not alter application source files.

Do not delete previous Veracode output.

Do not create a second Veracode scan command during the same invocation.
