# 固定公网HTTPS与腾讯云部署准备

目标更新：2026-11-25前完成公网接入与声网／DeepSeek／Qwen TTS语音联调，不再以11月22日校赛安排作为验收期限。本文的域名或IP方案均须实际云端验证；新域名备案不能保证在该日期前完成。

11月21日配置准备记录：新增IP版Compose、Caddyfile和续期脚本，证书私有目录已排除Git与Docker构建上下文；Compose结构与配置解析通过。本机Docker引擎当前不可连接，Caddy容器语法／运行验证未完成。未在腾讯云执行安装、证书签发或部署，不将本地文件准备记为公网验收通过。

**最新授权与方案：以本项目为主，停止旧Nginx和3000端口Node服务，使用独立Caddy。** 用户已明确允许停止这两个旧服务，不再要求保留它们的可用性。停止后先核实80／443／3000空闲，再执行本文后部“无域名：固定公网IP证书操作步骤”，使用compose.public-ip.yaml。下方Nginx复用步骤保留作历史备选，本轮不执行，不使用compose.nginx.yaml。停止服务不等于删除程序或旧项目文件。

## 本轮首先执行：释放旧服务端口

将scripts/release-server-ports.py及scripts/install-docker-ubuntu24.sh上传到服务器，例如/tmp下。在服务器终端依次运行：

```sh
sudo python3 /tmp/release-server-ports.py
sudo python3 /tmp/release-server-ports.py --stop
sudo sh /tmp/install-docker-ubuntu24.sh
sudo docker run --rm hello-world
sudo docker compose version
```

第一个命令只检查，第二个命令执行已授权停止：禁用并停止nginx.service；只向当前监听TCP 3000的Node进程发SIGTERM，不按旧PID执行，不结束所有Node进程，不触碰SSH。若3000已换成其他程序，脚本停止而不做变更；若Node由PM2／systemd等自动拉起，脚本输出管理器unit及仍占用的端口，不循环强杀。根据实际管理器再停止对应旧应用的自动启动；不能为了停一个应用而直接停掉SSH或整个多应用管理器。

两个脚本完成后，把结果发来，确认端口空闲与Docker可用，再上传项目镜像／配置并申请IP证书。脚本当前仅做了本地Python语法及帮助入口检查，没有在用户服务器执行停止或安装。

## 已确认服务器信息与先前Nginx保留方案（本轮不执行）

| 项目 | 检查结果 |
|---|---|
| 系统／架构 | Ubuntu 24.04.4 LTS／x86_64 |
| 内存 | 总计1967MiB，可用约1487MiB |
| Swap | 约10GiB，剩余约10GiB |
| 系统盘 | 50GB，剩余约24GB |
| 已有监听 | Nginx 80、Node 3000、SSH 22 |
| 443 | 本次ss输出未显示监听，仍需核对Nginx配置 |
| Docker | 未安装 |
| 腾讯云防火墙 | 截图显示TCP 80／443允许全部IPv4访问；22和33943已有规则，保留原用途 |
| 声网截图 | 项目启用、主要证书启用可确认；未显示引擎／RTM服务详情或赠送包明细 |

先不购买域名，以`https://服务器当前公网IPv4`作为可信IP证书联调目标，未签发证书／部署服务前不能当作已可用地址。腾讯云官方说明仅通过公网IP直接访问测试可暂不备案；免费二级域名不自动免除大陆服务器的域名接入要求。长期域名发布另行处理。以下五步仅供需要保留旧Nginx的情况，本轮按最新授权不执行。

### 第一步：安装Docker Engine和Compose

使用scripts/install-docker-ubuntu24.sh。脚本仅适用于本次确认的Ubuntu 24.04 amd64，安装Docker官方apt仓库的docker-ce、CLI、containerd.io、buildx、Compose插件。发现冲突软件包会停止，不自动卸载。上传脚本后在服务器执行：

```sh
sudo sh install-docker-ubuntu24.sh
sudo docker run --rm hello-world
sudo docker compose version
```

不安装Windows Docker Desktop，也不使用snap／podman-docker替代此方案。若Docker官方仓库访问失败，保留错误再调整下载方式，不连续混装不同来源的软件包。

### 第二步：只读检查Nginx和系统防火墙

```sh
sudo nginx -t
sudo nginx -T 2>/dev/null | grep -nE '^# configuration file|^[[:space:]]*(listen|server_name)[[:space:]]'
sudo ufw status verbose
sudo ss -lntp | grep -E ':(80|443|3000|8000|13000|5432)[[:space:]]'
```

该过滤只输出Nginx配置文件名及listen／server_name，不输出可能含认证信息的其他配置。根据结果定位实际处理公网IP请求的80端口server块，备份对应文件后，在其中加入infra/nginx/zhiyu-acme.conf的location或include。若存在server级return／rewrite，需进一步核对后再集成。不要用新的默认站点整体替换旧站点，不关闭已有Nginx／Node服务。

### 第三步：证书使用webroot验证

专用HTTP验证目录为`/var/lib/zhiyu-acme`，Nginx片段只处理`/.well-known/acme-challenge/`，其余旧服务路径保留。先创建测试文件，并从本机通过HTTP检查能够读到，再申请证书：

```sh
sudo install -d -m 0755 /var/lib/zhiyu-acme/.well-known/acme-challenge
printf '%s\n' zhiyu-acme-ok | sudo tee /var/lib/zhiyu-acme/.well-known/acme-challenge/zhiyu-check.txt >/dev/null
sudo nginx -t
sudo systemctl reload nginx
```

在自己的电脑访问`http://实际IP/.well-known/acme-challenge/zhiyu-check.txt`，应得到zhiyu-acme-ok。此步骤通过后，服务器项目目录统一为`/opt/zhiyu`，运行：

```sh
cd /opt/zhiyu
sudo mkdir -p infra/private/letsencrypt
sudo docker run --rm certbot/certbot:latest --version
PUBLIC_IP="你的当前公网IPv4"
ACME_EMAIL="你的邮箱"
sudo docker run --rm \
  -v /opt/zhiyu/infra/private/letsencrypt:/etc/letsencrypt \
  -v /var/lib/zhiyu-acme:/var/www/acme \
  certbot/certbot:latest certonly --webroot --webroot-path /var/www/acme \
  --preferred-profile shortlived --ip-address "$PUBLIC_IP" \
  --cert-name zhiyu-ip --agree-tos --email "$ACME_EMAIL"
```

Certbot版本需至少5.4，证书需为正式可信CA签发。本机IP必须与证书一致；无须申请域名。不要使用--standalone抢占当前Nginx的80端口。

### 第四步：部署新容器和HTTPS站点

本机构建镜像并上传后，在服务器导入。服务器.env设置APP_ENV=staging、PUBLIC_ORIGIN与INTEGRATION_PUBLIC_URL为同一个https://实际IP，保留云调用／语音开关false直到HTTPS检查通过。PUBLIC_HOST可留空，PRIVATE密钥只通过安全文件传输保存。

```sh
cd /opt/zhiyu
sudo docker load -i zhiyu-images.tar
sudo docker compose -f compose.yaml -f compose.cloud.yaml -f compose.nginx.yaml config --quiet
sudo docker compose -f compose.yaml -f compose.cloud.yaml -f compose.nginx.yaml up --no-build -d
sudo docker compose -f compose.yaml -f compose.cloud.yaml -f compose.nginx.yaml ps
```

compose.nginx.yaml用!override替换前端映射，要求Compose至少2.24.4；结果只绑定127.0.0.1:13000，不保留原3000映射。后端8000和数据库5432也只绑定本机。本地配置解析已核对这一点。

将infra/nginx/zhiyu-ip.conf.example中的__PUBLIC_IP__替换为当前公网IP，确认不存在冲突的443站点后，作为新增HTTPS站点启用。模板引用/opt/zhiyu/infra/private/letsencrypt/live/zhiyu-ip下的证书；如项目目录不同，必须修改证书路径。先nginx -t成功，再systemctl reload nginx。已有80端口的微信回调及3000应用保留。

新增站点将/api、/integrations、/health代理到127.0.0.1:8000，页面代理到127.0.0.1:13000；流式路径关闭缓冲、缓存及自动上游重试，避免重复请求。服务器外访问HTTPS健康检查无证书警告才算该阶段通过。

### 第五步：续期及后续语音

这台服务器使用infra/renew-ip-cert-nginx.sh（不是Caddy版renew-ip-cert.sh），配置root每12小时运行，并先做一次Certbot webroot --dry-run续期演练。脚本续期后nginx -t并平滑reload。证书与私钥目录已排除Git／Docker构建上下文。

按用户当前要求，先完成前述服务器与HTTPS步骤，再核对声网引擎／RTM与赠送额度、开启语音，不在本阶段创建云Agent或执行真实外呼。截图余额为0不等于赠送包一定为0；右侧付费套餐广告不构成购买要求。当前截图不能作为权限或额度验收证据。

来源：[Docker Ubuntu安装](https://docs.docker.com/engine/install/ubuntu/)、[Compose映射替换规则](https://docs.docker.com/reference/compose-file/merge/)、[Nginx流式代理](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)、[Certbot IP证书](https://letsencrypt.org/2026/03/11/shorter-certs-certbot.html)、[腾讯云IP测试说明](https://cloud.tencent.com/document/api/243/19630)。

## 什么时候需要公网

本地开发、文本业务回归、DeepSeek文本探针、百炼TTS探针只需要本机能访问供应商，不要求入站公网。当前声网云端对话式AI方案要主动调用本项目LLM入口和Qwen TTS转换入口，因此完整语音联调需要声网能访问的固定HTTPS后端。前端可以先在localhost开发，数据库始终不公开。

公网HTTPS入口不等于必须新买域名；可使用团队／学校已有的合规域名与部署资源。不要把动态临时隧道当成交付条件，不用自签名证书冒充已通过云端验证。

无域名还有“固定公网IP＋可信IP证书”的候选方式。Let's Encrypt已在2026年开放IPv4／IPv6证书，使用shortlived配置、有效期160小时，必须自动续期；官方Certbot教程要求5.4及以上以使用IP证书的webroot模式。当前仓库Caddyfile是域名配置，不能仅把PUBLIC_HOST改成IP就认定会签发可信IP证书。采用IP入口前须另外核实证书客户端／自动续期、80／443连通、腾讯云当前测试接入要求，以及声网对该HTTPS IP入口的实际访问；尚未在用户服务器执行。没有域名不应阻断本地模型／TTS开发，也不代表完整声网云端链路已经可用。

参考：[Let's Encrypt IP证书开放说明](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability)、[Certbot IP证书教程](https://letsencrypt.org/2026/03/11/shorter-certs-certbot.html)。

## 初次截图与通用独立主机方案

初次截图确认腾讯云轻量应用服务器位于北京，2核／2GB内存／50GB磁盘／4Mbps带宽，已有OpenClaw应用；当时系统与端口尚未确认。后续实际检查结果和这台主机的Nginx专用方案已更新在本文前部。本节及后续独立Caddy方案保留作通用参考，当前主机不直接执行。公网IP由本人控制台读取，不在仓库固定。

先使用腾讯云服务器页的“登录”进入终端，只读执行：

```sh
cat /etc/os-release
uname -m
free -m
df -h
sudo ss -lntp
docker version
docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}'
```

这些输出用于确认是否有空间部署新项目，不能凭实例名称推断Ubuntu版本。不得直接覆盖OpenClaw目录或停止现有应用。80／443若已被现有代理占用，应整合代理站点，不能直接启动第二个绑定相同端口的网关。

2GB内存的服务器优先使用本机预构建镜像、单并发联调，不在服务器运行Next.js生产构建；是否需额外资源由上述盘点结果决定，不先要求购买升级。

## 如何检查已有域名与备案

1. 腾讯云顶部搜索“域名注册”，进入域名列表／我的域名，查看是否有本人持有、未到期的域名，以及实名状态。
2. 顶部搜索“网站备案”，进入ICP备案控制台，查看已备案信息，匹配域名、备案主体、审核结果和接入状态。首次备案申请中的“审核中”不能视为备案完成；其他接入商备案也需要确认腾讯云接入要求。
3. 服务器页的“域名解析”只是DNS配置入口，不能据此证明已注册域名或完成备案。

截图中的“注册新域名”弹窗是购买流程：当前账号无可用实名信息模板，需要先创建并审核模板。模板实名认证一般1–3个工作日；注册成功和实名认证都不等于ICP备案成功。腾讯云备案初审预计1–2个工作日，管局审核不超过20个工作日，首次备案未通过前不能按已上线方案提供域名服务。11月25日前的联调目标不能依赖新域名按时完成全部环节。

若无现成域名／接入条件，可评估下述可信IP证书方案，并联系老师确认已有合规HTTPS资源；长期域名可并行准备。北京实例使用域名对外服务需按腾讯云大陆备案要求处理，不用切换端口绕过。

## 域名条件满足后的DNS和端口

假设实际分配的域名为`voice.example.com`（示例，须替换）：

| 项目 | 配置 |
|---|---|
| DNS类型 | A |
| 主机记录 | voice |
| 记录值 | 腾讯云控制台中的服务器公网IPv4 |
| HTTPS入口 | https://voice.example.com |
| 腾讯云防火墙／系统防火墙 | 确认TCP 80、443可入站，SSH仅供管理 |
| PostgreSQL／FastAPI／Next.js宿主端口 | 保持127.0.0.1绑定，不开放公网5432／8000／3000 |

确认DNS已生效，且不存在指向其他服务器的冲突AAAA记录。现有`infra/Caddyfile`会反向代理`/api/*`、`/integrations/*`、`/health/*`至后端，其他请求至前端；流式路径关闭缓冲。Caddy数据卷保存证书，符合DNS／端口条件后自动申请和续期证书。

服务器专用`.env`关键配置：

```dotenv
APP_ENV=staging
PUBLIC_HOST=voice.example.com
PUBLIC_ORIGIN=https://voice.example.com
INTEGRATION_PUBLIC_URL=https://voice.example.com
ENABLE_FAULTS=false
MODEL_PROVIDER=deepseek
TTS_PROVIDER=qwen
VOICE_ENABLED=false
VOICE_WRITES_ENABLED=false
CLOUD_CALLS_ENABLED=false
FREE_QUOTA_CONFIRMED=true
```

另填数据库密码、服务端Cookie／入口随机密钥、联调登录密码和供应商凭证，均保存在服务器私有.env。STAGING_PASSWORD用于公网联调登录，开发登录在staging被禁用。先保留云调用关闭，HTTPS验证及探针通过后再开启；语音写操作继续关闭。

## 使用已有Compose部署

先在本机构建并验证镜像，确认服务器架构与镜像一致后导出：

```powershell
docker compose build
docker save -o .cache/zhiyu-images.tar zhiyu-backend:latest zhiyu-frontend:latest
```

通过已配置的SSH／SFTP传输镜像、当前确认版本的Compose文件和infra/Caddyfile到独立项目目录。服务器没有取得当前工作区文件前，不用旧Git提交冒充当前版本。凭证不上传到Git仓库。

服务器中在项目目录执行（资源、端口、DNS、备案满足后）：

```sh
docker load -i zhiyu-images.tar
docker compose -f compose.yaml -f compose.cloud.yaml -f compose.public.yaml config --quiet
docker compose -f compose.yaml -f compose.cloud.yaml -f compose.public.yaml up --no-build -d
docker compose -f compose.yaml -f compose.cloud.yaml -f compose.public.yaml ps
```

`--no-build`使用已导入的默认镜像名；postgres:18和caddy:2由服务器拉取。此文档尚未在用户服务器执行，不代表公网部署通过。

## 验收顺序

声网控制台先选择与.env中AGORA_APP_ID一致的项目，核对项目启用、主要证书启用、实时音视频RTC／实时消息RTM／对话式AI引擎的可用状态及赠送额度。ASR先保持中文zh-CN。TTS使用已接通的百炼Qwen，由本项目后端转换为generic_http，不需要额外购买另一个TTS供应商。实际LLM／TTS入口地址和会话绑定鉴权由后端创建Agent时生成，不能在控制台直接连接DeepSeek绕开业务后端。

1. 从服务器外部请求`https://实际域名/health/ready`，验证DNS、可信证书、HTTP状态与真实后端；能打开腾讯云默认页面不算通过。
2. 检查HTTPS联调登录、未鉴权访问拒绝、会话隔离和SSE流式传输。
3. 两个本地供应商探针通过后，再启用云配置。真实声网请求必须带有效会话绑定密钥到达`/integrations/agora/sessions/{sid}/v1/chat/completions`和`.../v1/audio/speech`；不能用无鉴权探测的401当作完整协议通过。
4. 完成三轮语音、自然与手动打断、超时、断网和Agent清理，并保存实测证据。尚未执行时不宣布语音时延达标。

## 无域名：固定公网IP证书操作步骤

本路径使用独立的compose.public-ip.yaml与infra/Caddyfile.ip，不混用域名版compose.public.yaml。IP证书短期有效，需要自动续期。当前仅准备了文件，尚未在用户服务器签发证书或验证声网能访问IP入口。

前提：前面的系统／资源检查完成，服务器已有Docker及Compose，80／443没有被其他程序占用，公网IPv4稳定且已确认入站规则。若端口已被OpenClaw或其他应用占用，先保留输出，由维护者调整现有代理；不要直接停止现有服务。IP入口用于联调测试，部署前确认腾讯云当前接入要求；它不代替长期网站使用域名时的备案。

1. 在本机构建并导出当前确认版本镜像（前文命令），通过SFTP上传到服务器独立项目目录。另上传compose.yaml、compose.cloud.yaml、compose.public-ip.yaml、infra/Caddyfile.ip、infra/renew-ip-cert.sh及私有.env，保持相对目录。上传.env使用安全文件传输，不发聊天／Git。镜像架构必须与服务器uname -m匹配。
2. 在服务器项目目录创建证书目录并检查Certbot版本：

```sh
mkdir -p infra/private/letsencrypt infra/private/acme-webroot
docker run --rm certbot/certbot:latest --version
```

Certbot版本须至少5.4。infra/private已加入.gitignore和.dockerignore，证书与私钥不进入仓库或镜像。

3. 确认80端口空闲后，先取得IP证书。下面两个变量替换为当前控制台公网IP及本人邮箱，不能原样复制占位符：

```sh
PUBLIC_IP="你的服务器公网IPv4"
ACME_EMAIL="你的邮箱"
docker run --rm -p 80:80 \
  -v "$(pwd)/infra/private/letsencrypt:/etc/letsencrypt" \
  certbot/certbot:latest certonly --standalone \
  --preferred-profile shortlived --ip-address "$PUBLIC_IP" \
  --cert-name zhiyu-ip --agree-tos --email "$ACME_EMAIL"
```

此命令向正式CA申请可信证书，证书须包含公网IP。初次排查可先使用--staging，但测试CA证书不被公网客户端信任，最终必须取得正式证书后才能进行声网验收。不要把证书私钥内容发出来。

4. 服务器私有.env使用以下值，其余账号字段沿用已确认配置；数据库与项目内部随机密钥使用服务器专用值：

```dotenv
APP_ENV=staging
PUBLIC_IP=你的服务器公网IPv4
PUBLIC_ORIGIN=https://你的服务器公网IPv4
INTEGRATION_PUBLIC_URL=https://你的服务器公网IPv4
PUBLIC_HOST=
MODEL_PROVIDER=deepseek
TTS_PROVIDER=qwen
FREE_QUOTA_CONFIRMED=true
CLOUD_CALLS_ENABLED=false
VOICE_ENABLED=false
VOICE_WRITES_ENABLED=false
ENABLE_FAULTS=false
```

5. 导入镜像并启动HTTPS项目：

```sh
docker load -i zhiyu-images.tar
docker compose -f compose.yaml -f compose.cloud.yaml -f compose.public-ip.yaml config --quiet
docker compose -f compose.yaml -f compose.cloud.yaml -f compose.public-ip.yaml up --no-build -d
docker compose -f compose.yaml -f compose.cloud.yaml -f compose.public-ip.yaml ps
```

6. 从本人电脑访问`https://实际公网IP/health/ready`，应无证书警告并返回ready。再验证登录。需要的是可信证书访问成功，不能用curl -k或浏览器忽略警告替代。
7. 配置证书续期：infra/renew-ip-cert.sh使用Caddy暴露的ACME webroot续期，并强制重载证书，避免为了续期中断80端口。用`sudo crontab -e`为root加入每12小时任务，将下列两处路径替换为服务器项目绝对路径（由pwd获得）：

```cron
0 */12 * * * /bin/sh /项目绝对路径/infra/renew-ip-cert.sh >> /项目绝对路径/infra/private/cert-renew.log 2>&1
```

首次部署应验证续期演练及重载，不只添加定时任务：

```sh
docker run --rm \
  -v "$(pwd)/infra/private/letsencrypt:/etc/letsencrypt" \
  -v "$(pwd)/infra/private/acme-webroot:/var/www/acme" \
  certbot/certbot:latest renew --cert-name zhiyu-ip \
  --webroot --webroot-path /var/www/acme --dry-run
sh infra/renew-ip-cert.sh
```

8. 公网HTTPS与登录检查通过、声网额度确认后，将CLOUD_CALLS_ENABLED=true及VOICE_ENABLED=true并用同一组-f参数重新up -d。VOICE_WRITES_ENABLED继续false。页面点击开始语音后，确认声网能实际请求自定义LLM和TTS入口；若平台拒绝IP形式URL，则保留失败证据并切换已有合规域名，不能把本地成功当作云端通过。

## 用户先完成哪些操作

首先提供服务器只读检查结果、当前公网IP、可用登录方式（只说方式，不发送密码／私钥），并在腾讯云防火墙中确认80／443规则。在声网控制台确认同一项目的RTC／RTM／对话式AI服务与额度。准备好这些后，再按实际操作系统和现有端口选择域名版或IP版部署；不要在系统和端口未确认时批量复制安装命令。

参考：[声网自定义模型](https://doc.shengwang.cn/doc/convoai/restful/user-guides/custom-llm)、[声网自定义TTS](https://github.com/AgoraIO/docs-portal/blob/main/content/docs/en/ai/build/custom-model-integration/custom-tts.mdx)、[腾讯云是否需要备案](https://cloud.tencent.com/document/api/243/19630)、[腾讯云域名注册与实名](https://cloud.tencent.com/document/product/242/39039)、[备案审核时间](https://cloud.tencent.com/document/faq/243/19650)、[Caddy HTTPS要求](https://caddyserver.com/docs/quick-starts/https)。
