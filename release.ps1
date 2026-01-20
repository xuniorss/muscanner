param(
  [Parameter(Mandatory=$true)]
  [string]$ReleaseVersion,
  [switch]$NoPush,
  [switch]$CreateRelease
)

& "$PSScriptRoot\tools\release.ps1" -ReleaseVersion $ReleaseVersion -NoPush:$NoPush -CreateRelease:$CreateRelease
exit $LASTEXITCODE
