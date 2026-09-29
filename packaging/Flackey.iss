; Inno Setup 6 script for Flackey-Setup.exe. Build it with packaging/build_windows.ps1, which runs
;   ISCC /DAppVersion=X.Y.Z packaging\Flackey.iss
; after PyInstaller has produced packaging/build/dist/Flackey. Relative paths below resolve from this
; file's folder (packaging/).
;
; A per-user install into %LOCALAPPDATA%\Programs\Flackey: no UAC prompt, which matters twice -- once for
; a first install by someone who is not an administrator, and again every time the in-app Update runs a
; newer Setup.exe over the old one.
;
; The installer is unsigned (see packaging/README-windows.md). Kept pure ASCII: ISCC reads a BOM-less
; script as the ANSI code page.

#ifndef AppVersion
  #error Pass the version: ISCC /DAppVersion=X.Y.Z Flackey.iss (build_windows.ps1 does)
#endif

[Setup]
; Never change this. Windows identifies an installed program by it: a new value makes the next release
; install side by side with the old one instead of upgrading it. The doubled brace is Inno's escape.
AppId={{27664D41-7301-4197-B8C3-27D05631C1F6}
AppName=Flackey
AppVersion={#AppVersion}
AppVerName=Flackey {#AppVersion}
AppPublisher=Flackey
AppPublisherURL=https://eyaldelarea.github.io/flackey/
AppSupportURL=https://github.com/EyalDelarea/flackey/issues
AppUpdatesURL=https://github.com/EyalDelarea/flackey/releases
; Properties > Details of Setup.exe itself, so the file SmartScreen asks about is not anonymous.
VersionInfoVersion={#AppVersion}
VersionInfoCompany=Flackey
VersionInfoProductName=Flackey
VersionInfoDescription=Flackey installer
DefaultDirName={localappdata}\Programs\Flackey
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
; x64 only; Windows on ARM runs it through its x64 emulation, which "x64compatible" admits.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; The in-app Update starts this Setup.exe and then quits, but Flackey (or an ffmpeg it started) may still
; be holding files. Restart Manager closes whatever has them open instead of failing mid-copy; *.pyd is
; added to the default filter because PyInstaller's extension modules are DLLs by another name.
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
SetupIconFile=build\Flackey.ico
UninstallDisplayIcon={app}\Flackey.exe
UninstallDisplayName=Flackey
WizardStyle=modern
OutputDir=build
OutputBaseFilename=Flackey-Setup
Compression=lzma2/max
SolidCompression=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; An upgrade copies the new build over the old one. PyInstaller's _internal folder changes shape between
; releases, and a .pyd or DLL left behind by an older version can be imported instead of the new one.
; Only this folder, which is entirely ours: never {app} itself, which is whatever folder the person chose.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "build\dist\Flackey\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Flackey"; Filename: "{app}\Flackey.exe"
Name: "{autodesktop}\Flackey"; Filename: "{app}\Flackey.exe"; Tasks: desktopicon

[Run]
; No skipifsilent: the in-app Update runs this installer and quits, and this checkbox is what brings the
; app back afterwards.
Filename: "{app}\Flackey.exe"; Description: "{cm:LaunchProgram,Flackey}"; Flags: nowait postinstall

; No [UninstallDelete] on purpose. The uninstaller removes exactly what [Files] installed, and nothing
; else: the settings, the database, the Telegram session and the log live in %APPDATA%\Flackey and belong
; to the person, who may be uninstalling only to reinstall. Their music is in a folder they chose.
