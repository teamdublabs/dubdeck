# Hyper-V provider

> **Experimental.** Implemented and unit-tested against the documented PowerShell
> / Hyper-V command shapes, but not yet exercised against a live Windows host in
> this project's CI. It should work; you are an early user — bug reports (and
> captured `Get-VM` / `Get-VMCheckpoint` JSON from a real host) are very welcome.

Manages **Hyper-V virtual machines** on a Windows host. Dubdeck reaches the host
over SSH and drives it entirely through **PowerShell** — every command emits JSON
(`ConvertTo-Json -Compress`) and Dubdeck parses the JSON, never PowerShell's
human table output. Resources are `kind: vm`, keyed by **VM name**.

## Capabilities

| Capability | Hyper-V command |
|---|---|
| start           | `Start-VM -Name '<vm>'` |
| stop            | `Stop-VM -Name '<vm>' -Force` (graceful in-guest shutdown; escalates to force on timeout) |
| force_stop      | `Stop-VM -Name '<vm>' -TurnOff -Force` (hard power-off) |
| suspend         | `Save-VM -Name '<vm>'` (saves state to disk; `Start-VM` resumes) |
| snapshot_list   | `Get-VMCheckpoint -VMName '<vm>'` |
| snapshot_create | `Checkpoint-VM -Name '<vm>' -SnapshotName '<name>'` |

No `logs` or `disk_stats` — neither is a first-class single-call Hyper-V
operation, so they're out of scope for v1. The `-Force` on the graceful `stop`
only suppresses the interactive confirmation prompt (which would otherwise hang a
non-interactive SSH session); the shutdown is still graceful. Snapshots are
Hyper-V **checkpoints**; `Get-VMCheckpoint`/`Checkpoint-VM` require Windows Server
2016 / Windows 10 or newer. Restore and delete are deliberately absent — the same
no-destructive-ops boundary as every other provider.

## Windows host setup (OpenSSH + PowerShell)

1. **Install the OpenSSH Server feature** (elevated PowerShell):

   ```powershell
   Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
   Start-Service sshd
   Set-Service -Name sshd -StartupType Automatic
   ```

2. **Make PowerShell the default SSH shell** so commands run in PowerShell, not
   `cmd.exe`:

   ```powershell
   New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell `
     -Value "C:\Program Files\PowerShell\7\pwsh.exe" -PropertyType String -Force
   ```

   (Use the path to `pwsh.exe` for PowerShell 7, or
   `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe` for built-in
   Windows PowerShell 5.1 — the provider's commands work on both. PowerShell 7 is
   recommended; its JSON serialisation is cleaner.)

3. **Key-based auth, and mind the administrators ACL gotcha.** For a *normal*
   user, the public key goes in `C:\Users\<user>\.ssh\authorized_keys`. For a
   member of the local **Administrators** group, OpenSSH ignores that file and
   reads `C:\ProgramData\ssh\administrators_authorized_keys` instead, which must
   be owned by `Administrators`/`SYSTEM` only:

   ```powershell
   $f = "C:\ProgramData\ssh\administrators_authorized_keys"
   # (put your public key in $f first)
   icacls $f /inheritance:r
   icacls $f /grant "Administrators:F" "SYSTEM:F"
   ```

   If key auth silently fails for an admin user, this ACL is almost always why.

4. **The user must be able to manage Hyper-V** — either a local Administrator or a
   member of the **Hyper-V Administrators** group.

5. **Force UTF-8 output (recommended).** If `Get-VM` output arrives mangled or
   with a leading byte-order mark, set the session output encoding — e.g. add to
   the user's PowerShell profile:

   ```powershell
   [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
   ```

   Dubdeck strips a leading UTF-8 BOM defensively, but a correctly-configured
   shell avoids the issue entirely.

## Config

Hyper-V is a command provider like Docker or libvirt — it binds to an SSH host:

```yaml
hosts:
  winhost:
    transport: ssh
    address: 192.0.2.30     # IP/hostname the backend can SSH to
    user: labadmin          # a Hyper-V administrator
    stats: null             # Windows host load/mem stats aren't collected in v1

providers:
  - id: winhost-hyperv
    type: hyperv
    host: winhost

groups:
  windows-lab:
    label: "Windows Lab"
    auto: winhost-hyperv    # track the host's live VM list
```

## A note on quoting (for contributors)

Unlike the POSIX providers, Hyper-V command builders do **not** use `shlex.quote`.
Commands run in PowerShell, which has its own grammar; resource names are wrapped
in PowerShell single-quoted literals where the only metacharacter is the single
quote itself (escaped by doubling). See `ps_quote` in `app/providers/hyperv.py`
and its injection tests — using POSIX quoting here would be both incorrect and
unsafe.
