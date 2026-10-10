#define AppVersion "0.1.0-beta.2"
#define NodeVersion "v24.21.0"
#define NodeArchive "node-v24.21.0-win-x64.zip"
#define NodeSHA256 "158f7685b44de51f6c0df1d153526cbcd3e1bc739a8dfc607721cef75de9e541"
#define FFmpegBuild "autobuild-2026-09-30-13-08"
#define FFmpegArchive "ffmpeg-N-127021-ge0c94b2d1c-win64-lgpl-shared.zip"
#define FFmpegSHA256 "2cc3dd0baf74ebd68a3fceaf771df8351a7a6d69694d05f43b16bb0c966ec909"

[Setup]
AppId={{B89BD8C2-E973-4C8A-B577-76A2884FC25B}
AppName=Downetic
AppVersion={#AppVersion}
AppPublisher=Downetic
AppPublisherURL=https://github.com/taiguls67/downetic
AppSupportURL=https://github.com/taiguls67/downetic
DefaultDirName={localappdata}\Programs\Downetic
DefaultGroupName=Downetic
UninstallDisplayIcon={app}\Downetic.exe
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19045
WizardStyle=modern dynamic
Compression=lzma2/ultra64
SolidCompression=yes
SetupIconFile=..\downloader_app\assets\icons\main_ico.ico
OutputDir=..\dist\installer
OutputBaseFilename=Downetic-Setup-{#AppVersion}
ArchiveExtraction=full

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\Downetic.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{tmp}\DowneticNode\node-{#NodeVersion}-win-x64\node.exe"; DestDir: "{app}\runtime\node"; Flags: external ignoreversion
Source: "{tmp}\DowneticNode\node-{#NodeVersion}-win-x64\LICENSE"; DestDir: "{app}\runtime\node"; Flags: external ignoreversion
Source: "{tmp}\{#FFmpegArchive}"; DestDir: "{app}\runtime\ffmpeg"; Flags: external extractarchive recursesubdirs ignoreversion

[Icons]
Name: "{group}\Downetic"; Filename: "{app}\Downetic.exe"
Name: "{autodesktop}\Downetic"; Filename: "{app}\Downetic.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Downetic.exe"; Description: "{cm:LaunchProgram,Downetic}"; Flags: nowait postinstall skipifsilent

[Code]
var
  DownloadPage: TDownloadWizardPage;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage(
    'Downloading required components',
    'Downetic needs Node.js and FFmpeg for video downloads.',
    nil);
  DownloadPage.ShowBaseNameInsteadOfUrl := True;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Error: String;
begin
  if CurPageID = wpReady then begin
    DownloadPage.Clear;
    DownloadPage.Add(
      'https://nodejs.org/dist/{#NodeVersion}/{#NodeArchive}',
      '{#NodeArchive}',
      '{#NodeSHA256}');
    DownloadPage.Add(
      'https://github.com/BtbN/FFmpeg-Builds/releases/download/{#FFmpegBuild}/{#FFmpegArchive}',
      '{#FFmpegArchive}',
      '{#FFmpegSHA256}');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
        ExtractArchive(
          ExpandConstant('{tmp}\{#NodeArchive}'),
          ExpandConstant('{tmp}\DowneticNode'),
          '',
          True,
          nil);
        Result := True;
      except
        if DownloadPage.AbortedByUser then
          Log('Dependency download aborted by user.')
        else begin
          Error := Format('Unable to download or unpack required components: %s', [GetExceptionMessage]);
          SuppressibleMsgBox(Error, mbCriticalError, MB_OK, IDOK);
        end;
        Result := False;
      end;
    finally
      DownloadPage.Hide;
    end;
  end else
    Result := True;
end;
