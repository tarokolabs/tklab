# tklab

給講師用的工具：一道指令在 Proxmox VE 上為每位學員建一台預裝 [tk8s](https://github.com/tarokolabs/tk8s) 的 VM，並在 Guacamole 建好該學員的帳號與 SSH、瀏覽器兩條連線；課程結束一道指令整批清掉。講師機不需要裝 tk8s。

## 安裝（講師機器）

```bash
uv tool install git+https://github.com/tarokolabs/tklab
tklab init             # 互動式：問 PVE、儲存、bridge、Guacamole，然後全部建好
tklab create template  # 從 Debian cloud image 做範本，做一次
```

需要 PVE 8 以上、Guacamole 1.5 以上。

## 指令

```
tklab init [pve|guacamole] [--manual] [-f FILE] [--pve-url … --storage … --guacamole-url …]
tklab create template [--k8s 1.36.5] [--node NAME]
tklab create class <班名> --students N [--init-cluster default|ha|FILE.yaml] [--expires 2026-10-20] [--node NAME|auto] [--cores N] [--memory MiB]
tklab create class -f k8s-101.toml
tklab get classes
tklab describe class <班名> [--roster] [-o toml]
tklab delete class <班名> [--yes]
tklab delete class --expired [--yes]
```

`create class` 的旗標與 `-f` 互斥。定義檔是 TOML，`students` 給名字、`count` 給人數（二擇一）；`[vm]` 蓋過設定檔的預設，`[[students_override]]` 再蓋單一學員：

```toml
name = "k8s-101"
expires = 2026-10-20
init_cluster = "default"
students = ["alice", "bob", "carol"]

[vm]
memory = 16384

[[students_override]]
name = "carol"
memory = 32768
init_cluster = "ha"
```

`describe class <班名> -o toml` 會把現有班級倒回這種格式，改個班名就能再開一班。

`--init-cluster`（檔案裡 `init_cluster`）會在每台 VM 連上 Guacamole 之後，透過 qemu-guest-agent 以 `student` 身分建一個叢集並 `tkctl use`、`tkctl verify`，學員一登入就有一個驗證過的叢集，`~/.kube/config` 已指好。值有三種寫法，依字面分辨：`default` 是 tkctl 的預設叢集（`tkctl create cluster tk8s`）；一個 tk8s 的 preset 名字（小寫字母、數字與 `-`，例如 `ha`）會原樣交給 `tkctl create cluster tk8s --preset <名字>`，名單與內容看 [tk8s 的 README](https://github.com/tarokolabs/tk8s#readme)，tklab 自己不認得任何名字，打錯的在 VM 裡被 tkctl 拒絕；以 `.yaml` 結尾的路徑是一份 tk8s 的 `cluster.yaml`（檔案裡相對於班級檔，旗標相對於目前目錄），tklab 會先確認檔案存在、不超過 60 KiB、`metadata:` 底下直接有一行 `name:`（它用這個名字跑 `use` 與 `verify`，沒有 YAML 剖析器），再透過 guest agent 寫到 VM 的 `/var/lib/tklab/cluster.yaml` 跑 `tkctl create cluster -f`。班級檔頂層的 `init_cluster` 是全班預設，`[[students_override]]` 裡的 `init_cluster` 蓋過單一學員；沒給就不建叢集。`ha`（3 台 control plane、3 台 worker、兩台 router）在預設規格的學員 VM（8 核 24G）從 `create class` 到 ready 約 5 分鐘，verify 後用掉 5.5G 記憶體，不用加規格。preset 要範本裝的是 tk8s v2026.10.12 以上（`--preset` 從這版才有），太舊的範本只會在名冊的 error 欄看到 tkctl 的 `unrecognized arguments`。舊的 `--k8s` 與 `k8s = true` 已改名為 `--init-cluster default` 與 `init_cluster = "default"`。

同一個 PVE 節點上同時最多建兩個叢集（更多會互搶拉 image 的頻寬而逾時），每個最多等 20 分鐘；VM 內的輸出在 `/var/log/tklab-k8s.log`。失敗只會記在名冊的 error 欄，VM 與帳號都留著，重跑同一道 `create class` 只會重做叢集那一步：先以現在的值重寫 VM 的 tag，再 `tkctl delete cluster` 掉上一次留下的叢集重建（tkctl 遇到半個舊叢集會接著跑舊拓樸、不套新值），cluster.yaml 也會重新送一次；換了檔案裡的叢集名字時，舊名字的叢集留在 VM 裡，請自己 `tkctl delete cluster`。VM 的 tag 多一個 `k8s-<值>`（檔案是 `k8s-file`），`describe -o toml` 會把它寫回每位學員的 `[[students_override]]`；來自檔案的只能寫佔位值 `init_cluster = "cluster.yaml"` 加一行註解，請把它指回原本的檔案。這一步需要開班用的角色有 `VM.GuestAgent.Unrestricted`（舊安裝重跑一次 `tklab init pve` 就會補上）。

每位學員一台 `lab-<班名>-<學員>` 的 VM（tag `lab;class-<班名>;expires-<日期>`），Guacamole 一個帳號（有名字的學員用名字，編號的用 `<班名>-<編號>`）與兩條連線：`<班名>-<學員> SSH` 和 `<班名>-<學員> Browser`（RDP 進去就是一個 Firefox 視窗，關掉瀏覽器連線就結束；沒有桌面）。名冊（VMID、IP、Guacamole 帳密、VM 的 `student` 密碼、失敗原因）寫在 `$XDG_STATE_HOME/tklab/<班名>.csv`（預設 `~/.local/state/…`），權限 0600，`describe class --roster` 會印出來。

`--parallel N`（預設 5）控制同時建幾台。某位學員失敗時其他人照建，名冊的 `error` 欄寫原因，結束碼 1；把原因排除後**再跑一次同一道 `create class`** 會續建：已完成的學員不動，失敗的從中斷處接下去（clone 失敗的重 clone、沒拿到 IP 的再等一次、Guacamole 沒建好的補建）。班級已有 VM 但名冊不在時會拒絕，避免建出重複的 VM。

結束碼：0 成功；1 有學員失敗、刪除有殘留或 PVE／Guacamole 回錯（一行訊息，不是 traceback）；2 用法或設定錯誤。`TK_ASSUME_YES=1` 等同 `--yes`。

## 設定檔

`pve.clone_storage`（例如 `"local-lvm"`）設了以後，學員 VM 會 **full clone** 到目標節點的本機儲存，磁碟 I/O 不再經過 NAS，開班每台約 1 到 2 分鐘；`node = "auto"` 會挑該儲存剩餘空間最多的節點、剩不到 32 GiB 的跳過；`pve.nodes` 可以限制 `auto` 只用哪幾台（例如 `["pve-node6", "pve-node7", "pve-node8", "pve-node9"]`）。沒設就是 linked clone，11 秒一台，但所有 I/O 都走共用儲存。互動式 `init` 看到節點有 lvmthin／zfs 儲存時會問這題。範本磁碟本身只有 `template.disk`（預設 12G）大，clone 後才放大到 `vm.disk`，因為 full clone 與遷移搬的是整顆虛擬磁碟。

`$XDG_CONFIG_HOME/tklab/config.toml`（預設 `~/.config/tklab/config.toml`）；`tklab init` 會產生含註解的範本。token 與密碼從環境變數或 `secrets.env`（0600，`init` 寫的）讀，環境變數優先。PVE 的 TLS 一律驗證，自簽 CA 用 `pve.ca_file` 指定（`/etc/pve/pve-root-ca.pem`）。

## 建立你的環境（做一次）

`tklab init` 依序做三件事，每一件都可以重跑（存在就補齊或略過）：

1. **設定檔** `$XDG_CONFIG_HOME/tklab/config.toml`（預設 `~/.config/tklab/config.toml`）。三種給值方式擇一：什麼都不給就進**互動式**（會先登入 PVE，只列出能放 images、snippets、import 的共用儲存與實際存在的 bridge、節點讓你選；Debian 映像的 sha512 自動抓）；**參數**（`--pve-url`、`--storage`、`--guacamole-url` 必填，其餘有預設）；或 **`-f FILE`** 用預先寫好的檔案（原樣複製，所以檔案本身要填好 `template.image_sha512`；互動式與參數模式會自動抓）。
2. **PVE**：建七個角色、使用者 `lab@pve`、pool、兩個 token、ACL，並把 PVE 的 CA 抓到 `pve-root-ca.pem` 給設定檔的 `ca_file`。需要管理員：互動式會隱藏輸入密碼，非互動式從 `TK_LAB_PVE_ADMIN_PASSWORD` 讀（帳號用 `--pve-admin`，預設 `root@pam`）。
3. **Guacamole**：建服務帳號 `tklab`、只給三個權限、有 TOTP 就幫它註冊。管理員密碼從隱藏輸入或 `TK_LAB_GUAC_ADMIN_PASSWORD` 讀（帳號 `--guacamole-admin`，預設 `guacadmin`）。

管理員密碼只在當下使用，不寫檔、不進 log。產生的 secret（兩個 PVE token、Guacamole 密碼與 TOTP secret）寫在 `$XDG_CONFIG_HOME/tklab/secrets.env`（0600）；同名環境變數有設會優先。

`tklab init pve`／`init guacamole` 只做一邊。範本重建後 PVE 會清掉 `/vms/3900` 的 ACL，跑一次 `init pve` 就補回。不想把管理員密碼交給工具的話，`--manual` 會印出等價的 `pveum` 腳本與 Guacamole 的設定清單。

然後 `tklab create template`（`pve.node` 是 `auto` 時要加 `--node`）。第一次跑只會把 cloud-init 的 snippet 寫到本機並印出 `scp` 指令：PVE 的 API 不收 snippets，得自己複製到儲存的 `snippets/` 一次；講師機如果掛了那個目錄，在設定檔填 `template.snippets_dir` 就會直接寫進去。再跑一次就會匯入映像、第一次開機裝 tk8s、xrdp 與瀏覽器，並以 `tkctl pull images` 把建叢集要的全部 image 拉進 tk8s 的共用 image store（需要 tk8s v2026.10.7 以上，CNI plugins 由 `tkctl create cluster --phase nodes/cni-plugins` 裝；沒設 `template.k8s` 或 `--k8s` 就跟那個 tk8s 版本的預設 Kubernetes 走，跟學員 VM 裡 `tkctl create cluster` 挑的一樣；釘別的版本只對叢集也建那個版本的班有用）。snippet 寫死了 tk8s 版本與預拉版本，所以設定改了再跑 `create template`，工具會發現儲存上的 snippet 已經過期，重寫本機那份並再印一次 `scp` 指令（有 `snippets_dir` 就直接改寫）、裝好 CNI plugins，關機後轉成範本，約 15 分鐘；之後每台學員 VM 建叢集都不必再下載。建置腳本任一步失敗 VM 會留著不關機，逾時後指令會叫你去看主控台。

## 權限

PVE：專用使用者 `lab@pve` 與兩個 token。開班用的 token 只能 clone 範本、管理 `lab` 資源池內的 VM、在一個儲存上配置空間、使用一個 bridge；建範本用的 token 才能寫映像、引用 snippets（PVE 要求 `Datastore.Allocate`，這個權限也能刪該儲存上的 volume，所以範本建好後建議 `pveum user token remove lab@pve tklab-build`，要重建再開）與讓節點抓網址。`/`、`/vms`、`/nodes` 上什麼都不給，所以碰不到 pool 外的 VM；`tklab init` 直接建立，`--manual` 可印出等價的 `pveum` 指令。`node = "auto"` 會把 VM 輪流放到線上的節點（token 看不到節點記憶體，看得到時會優先放最空的）。需要 PVE 8 以上（`VM.GuestAgent.Audit`）、Guacamole 1.5 以上。Guacamole：專用帳號只有 `CREATE_USER`、`CREATE_CONNECTION` 與 `CREATE_CONNECTION_GROUP`；它建出來的物件由它自己管理，碰不到別人的。學員在自己的 VM 裡有 sudo，VM 之間是硬體隔離。

## 授權

GPL-2.0-or-later。
