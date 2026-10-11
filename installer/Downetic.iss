#define AppVersion "0.1.0-beta.3"
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
AppVerName=Downetic v{#AppVersion}
AppPublisher=Downetic
AppPublisherURL=https://github.com/taiguls67/downetic
AppSupportURL=https://github.com/taiguls67/downetic
DefaultDirName={localappdata}\Programs\Downetic
DefaultGroupName=Downetic
UninstallDisplayName=Downetic v{#AppVersion}
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
Name: "english"; MessagesFile: "compiler:Default.isl"; LicenseFile: "licenses\license-en.txt"
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"; LicenseFile: "licenses\license-it.txt"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"; LicenseFile: "licenses\license-fr.txt"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"; LicenseFile: "licenses\license-de.txt"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"; LicenseFile: "licenses\license-es.txt"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"; LicenseFile: "licenses\license-pt-br.txt"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"; LicenseFile: "licenses\license-ja.txt"

[CustomMessages]
english.DesktopIconTaskDescription=Create a desktop shortcut
english.AdditionalIconsGroupDescription=Additional shortcuts:
english.LaunchDowneticDescription=Launch Downetic
english.DownloadPageTitle=Downloading required components
english.DownloadPageDescription=Downetic needs Node.js and FFmpeg for video downloads.
english.DownloadError=Unable to download or extract required components: %s
english.ExistingComponentsTitle=Use existing components
english.ExistingComponentsDescription=The following components were found on your PATH.
english.ExistingComponentsInstructions=Select the components you want to use without downloading a bundled copy. Leave a box unchecked to install Downetic's copy.
english.UseExistingNode=Use the existing Node.js installation (%s)
english.UseExistingFFmpeg=Use the existing FFmpeg installation (%s)
italian.DesktopIconTaskDescription=Crea un collegamento sul desktop
italian.AdditionalIconsGroupDescription=Collegamenti aggiuntivi:
italian.LaunchDowneticDescription=Avvia Downetic
italian.DownloadPageTitle=Download dei componenti richiesti
italian.DownloadPageDescription=Downetic richiede Node.js e FFmpeg per scaricare video.
italian.DownloadError=Impossibile scaricare o estrarre i componenti richiesti: %s
italian.ExistingComponentsTitle=Usa i componenti esistenti
italian.ExistingComponentsDescription=I seguenti componenti sono stati trovati nel PATH.
italian.ExistingComponentsInstructions=Seleziona i componenti da usare senza scaricarne una copia. Lascia deselezionata una casella per installare la copia di Downetic.
italian.UseExistingNode=Usa l'installazione esistente di Node.js (%s)
italian.UseExistingFFmpeg=Usa l'installazione esistente di FFmpeg (%s)
french.DesktopIconTaskDescription=Créer un raccourci sur le bureau
french.AdditionalIconsGroupDescription=Raccourcis supplémentaires :
french.LaunchDowneticDescription=Lancer Downetic
french.DownloadPageTitle=Téléchargement des composants requis
french.DownloadPageDescription=Downetic a besoin de Node.js et FFmpeg pour télécharger des vidéos.
french.DownloadError=Impossible de télécharger ou d’extraire les composants requis : %s
french.ExistingComponentsTitle=Utiliser les composants existants
french.ExistingComponentsDescription=Les composants suivants ont été trouvés dans le PATH.
french.ExistingComponentsInstructions=Sélectionnez les composants à utiliser sans télécharger de copie intégrée. Décochez une case pour installer la copie fournie par Downetic.
french.UseExistingNode=Utiliser l’installation Node.js existante (%s)
french.UseExistingFFmpeg=Utiliser l’installation FFmpeg existante (%s)
german.DesktopIconTaskDescription=Desktopverknüpfung erstellen
german.AdditionalIconsGroupDescription=Zusätzliche Verknüpfungen:
german.LaunchDowneticDescription=Downetic starten
german.DownloadPageTitle=Erforderliche Komponenten werden heruntergeladen
german.DownloadPageDescription=Downetic benötigt Node.js und FFmpeg für Video-Downloads.
german.DownloadError=Erforderliche Komponenten konnten nicht heruntergeladen oder entpackt werden: %s
german.ExistingComponentsTitle=Vorhandene Komponenten verwenden
german.ExistingComponentsDescription=Die folgenden Komponenten wurden im PATH gefunden.
german.ExistingComponentsInstructions=Wählen Sie Komponenten aus, die ohne eine gebündelte Kopie verwendet werden sollen. Deaktivieren Sie ein Kästchen, um die Downetic-Version zu installieren.
german.UseExistingNode=Vorhandene Node.js-Installation verwenden (%s)
german.UseExistingFFmpeg=Vorhandene FFmpeg-Installation verwenden (%s)
spanish.DesktopIconTaskDescription=Crear un acceso directo en el escritorio
spanish.AdditionalIconsGroupDescription=Accesos directos adicionales:
spanish.LaunchDowneticDescription=Iniciar Downetic
spanish.DownloadPageTitle=Descargando los componentes necesarios
spanish.DownloadPageDescription=Downetic necesita Node.js y FFmpeg para descargar vídeos.
spanish.DownloadError=No se pudieron descargar o extraer los componentes necesarios: %s
spanish.ExistingComponentsTitle=Usar componentes existentes
spanish.ExistingComponentsDescription=Se han encontrado los siguientes componentes en PATH.
spanish.ExistingComponentsInstructions=Selecciona los componentes que quieres usar sin descargar una copia incluida. Desmarca una casilla para instalar la copia de Downetic.
spanish.UseExistingNode=Usar la instalación existente de Node.js (%s)
spanish.UseExistingFFmpeg=Usar la instalación existente de FFmpeg (%s)
brazilianportuguese.DesktopIconTaskDescription=Criar um atalho na área de trabalho
brazilianportuguese.AdditionalIconsGroupDescription=Atalhos adicionais:
brazilianportuguese.LaunchDowneticDescription=Iniciar o Downetic
brazilianportuguese.DownloadPageTitle=Baixando os componentes necessários
brazilianportuguese.DownloadPageDescription=O Downetic precisa do Node.js e do FFmpeg para baixar vídeos.
brazilianportuguese.DownloadError=Não foi possível baixar ou extrair os componentes necessários: %s
brazilianportuguese.ExistingComponentsTitle=Usar componentes existentes
brazilianportuguese.ExistingComponentsDescription=Os seguintes componentes foram encontrados no PATH.
brazilianportuguese.ExistingComponentsInstructions=Selecione os componentes que deseja usar sem baixar uma cópia incluída. Desmarque uma opção para instalar a versão do Downetic.
brazilianportuguese.UseExistingNode=Usar a instalação existente do Node.js (%s)
brazilianportuguese.UseExistingFFmpeg=Usar a instalação existente do FFmpeg (%s)
japanese.DesktopIconTaskDescription=デスクトップにショートカットを作成
japanese.AdditionalIconsGroupDescription=追加のショートカット:
japanese.LaunchDowneticDescription=Downetic を起動
japanese.DownloadPageTitle=必要なコンポーネントをダウンロード中
japanese.DownloadPageDescription=動画をダウンロードするには Node.js と FFmpeg が必要です。
japanese.DownloadError=必要なコンポーネントをダウンロードまたは展開できませんでした: %s
japanese.ExistingComponentsTitle=既存のコンポーネントを使用
japanese.ExistingComponentsDescription=次のコンポーネントが PATH で見つかりました。
japanese.ExistingComponentsInstructions=同梱版をダウンロードせずに使用するコンポーネントを選択してください。Downetic 同梱版をインストールする場合は、チェックを外してください。
japanese.UseExistingNode=既存の Node.js を使用 (%s)
japanese.UseExistingFFmpeg=既存の FFmpeg を使用 (%s)

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIconTaskDescription}"; GroupDescription: "{cm:AdditionalIconsGroupDescription}"; Flags: unchecked

[Files]
Source: "..\dist\Downetic.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{tmp}\DowneticNode\node-{#NodeVersion}-win-x64\node.exe"; DestDir: "{app}\runtime\node"; Flags: external ignoreversion; Check: not UseExistingNode
Source: "{tmp}\DowneticNode\node-{#NodeVersion}-win-x64\LICENSE"; DestDir: "{app}\runtime\node"; Flags: external ignoreversion; Check: not UseExistingNode
Source: "{tmp}\{#FFmpegArchive}"; DestDir: "{app}\runtime\ffmpeg"; Flags: external extractarchive recursesubdirs ignoreversion; Check: not UseExistingFFmpeg

[Icons]
Name: "{group}\Downetic"; Filename: "{app}\Downetic.exe"
Name: "{autodesktop}\Downetic"; Filename: "{app}\Downetic.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Downetic.exe"; Description: "{cm:LaunchDowneticDescription}"; Flags: nowait postinstall skipifsilent

[Code]
var
  ComponentsPage: TInputOptionWizardPage;
  DownloadPage: TDownloadWizardPage;
  NodeOptionIndex: Integer;
  FFmpegOptionIndex: Integer;

function UseExistingNode: Boolean;
begin
  Result := False;
  if (ComponentsPage <> nil) and (NodeOptionIndex >= 0) then
    Result := ComponentsPage.Values[NodeOptionIndex];
end;

function UseExistingFFmpeg: Boolean;
begin
  Result := False;
  if (ComponentsPage <> nil) and (FFmpegOptionIndex >= 0) then
    Result := ComponentsPage.Values[FFmpegOptionIndex];
end;

procedure InitializeWizard;
var
  NodePath: String;
  FFmpegPath: String;
begin
  NodeOptionIndex := -1;
  FFmpegOptionIndex := -1;
  NodePath := FileSearch('node.exe', GetEnv('PATH'));
  FFmpegPath := FileSearch('ffmpeg.exe', GetEnv('PATH'));

  if (NodePath <> '') or (FFmpegPath <> '') then begin
    ComponentsPage := CreateInputOptionPage(
      wpSelectDir,
      ExpandConstant('{cm:ExistingComponentsTitle}'),
      ExpandConstant('{cm:ExistingComponentsDescription}'),
      ExpandConstant('{cm:ExistingComponentsInstructions}'),
      False,
      False);
    if NodePath <> '' then begin
      NodeOptionIndex := ComponentsPage.Add(
        Format(ExpandConstant('{cm:UseExistingNode}'), [NodePath]));
      ComponentsPage.Values[NodeOptionIndex] := True;
    end;
    if FFmpegPath <> '' then begin
      FFmpegOptionIndex := ComponentsPage.Add(
        Format(ExpandConstant('{cm:UseExistingFFmpeg}'), [FFmpegPath]));
      ComponentsPage.Values[FFmpegOptionIndex] := True;
    end;
  end;

  DownloadPage := CreateDownloadPage(
    ExpandConstant('{cm:DownloadPageTitle}'),
    ExpandConstant('{cm:DownloadPageDescription}'),
    nil);
  DownloadPage.ShowBaseNameInsteadOfUrl := True;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Error: String;
  DownloadPageShown: Boolean;
begin
  if CurPageID = wpReady then begin
    DownloadPage.Clear;
    DownloadPageShown := False;
    if not UseExistingNode then
      DownloadPage.Add(
        'https://nodejs.org/dist/{#NodeVersion}/{#NodeArchive}',
        '{#NodeArchive}',
        '{#NodeSHA256}');
    if not UseExistingFFmpeg then
      DownloadPage.Add(
        'https://github.com/BtbN/FFmpeg-Builds/releases/download/{#FFmpegBuild}/{#FFmpegArchive}',
        '{#FFmpegArchive}',
        '{#FFmpegSHA256}');
    try
      try
        if not UseExistingNode or not UseExistingFFmpeg then begin
          DownloadPage.Show;
          DownloadPageShown := True;
          DownloadPage.Download;
        end;
        if not UseExistingNode then
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
          Error := Format(ExpandConstant('{cm:DownloadError}'), [GetExceptionMessage]);
          SuppressibleMsgBox(Error, mbCriticalError, MB_OK, IDOK);
        end;
        Result := False;
      end;
    finally
      if DownloadPageShown then
        DownloadPage.Hide;
    end;
  end else
    Result := True;
end;
