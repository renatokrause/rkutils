#!/bin/python

import argparse
import csv
import getpass
import json
import ssl
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import urllib3
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_APP_TYPE = "Virtualization_Virtual_Servers_VSI"
DEFAULT_PERFORMANCE_POLICY_ID = "default_high"

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 60

WAIT_DEVICE_SECONDS = 240
POLL_SECONDS = 5


# =============================================================================
# LOG
# =============================================================================

def ts():
    return datetime.now().strftime("%H:%M:%S")


def log(msg=""):
    if msg:
        print(f"[{ts()}] {msg}", flush=True)
    else:
        print("", flush=True)


def title(msg):
    print("", flush=True)
    print("=" * 110, flush=True)
    print(f"[{ts()}] {msg}", flush=True)
    print("=" * 110, flush=True)


def ok(msg):
    log(f"[OK] {msg}")


def warn(msg):
    log(f"[WARN] {msg}")


def fail(msg):
    raise RuntimeError(msg)


def normalize_host(name):
    return (name or "").split(".")[0].lower()


# =============================================================================
# POWERSTORE
# =============================================================================

def ps_request(
    session,
    base_url,
    auth,
    method,
    path,
    token=None,
    json_body=None,
    params=None,
    description=None
):
    if description:
        log(f"PowerStore: {description}")

    headers = {
        "Accept": "application/json"
    }

    if json_body is not None:
        headers["Content-Type"] = "application/json"

    if token:
        headers["DELL-EMC-TOKEN"] = token

    try:
        r = session.request(
            method,
            f"{base_url.rstrip('/')}{path}",
            auth=auth,
            headers=headers,
            json=json_body,
            params=params,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )

    except requests.exceptions.ConnectTimeout:
        fail(f"Timeout conectando ao PowerStore em {path}")

    except requests.exceptions.ReadTimeout:
        fail(f"Timeout aguardando resposta do PowerStore em {path}")

    except requests.exceptions.RequestException as e:
        fail(f"Erro comunicando com PowerStore em {path}: {e}")

    if not r.ok:
        raise RuntimeError(
            f"PowerStore {method} {path} falhou: "
            f"HTTP {r.status_code}\n{r.text}"
        )

    if description:
        log(f"PowerStore: HTTP {r.status_code}")

    return r


def find_named(items, name):
    wanted = name.lower()

    for item in items:
        if (item.get("name") or "").lower() == wanted:
            return item

    return None


def powerstore_login(base_url, user, password):
    log(f"Conectando ao PowerStore {base_url} como {user}...")

    session = requests.Session()
    session.verify = False

    auth = (
        user,
        password
    )

    r = ps_request(
        session,
        base_url,
        auth,
        "GET",
        "/api/rest/login_session",
        description="autenticando"
    )

    token = r.headers.get(
        "DELL-EMC-TOKEN"
    )

    if not token:
        fail("PowerStore não retornou DELL-EMC-TOKEN.")

    ok("Login PowerStore concluído")

    return session, auth, token


def get_volume_group(
    session,
    base_url,
    auth,
    name
):
    r = ps_request(
        session,
        base_url,
        auth,
        "GET",
        "/api/rest/volume_group",
        params={
            "select": "id,name",
            "limit": 2000,
        },
        description=f'localizando Volume Group "{name}"'
    )

    obj = find_named(
        r.json(),
        name
    )

    if not obj:
        fail(
            f'Volume Group "{name}" não encontrado.'
        )

    ok(
        f'Volume Group encontrado: '
        f'{obj["name"]} ({obj["id"]})'
    )

    return obj


def get_host_group(
    session,
    base_url,
    auth,
    name
):
    r = ps_request(
        session,
        base_url,
        auth,
        "GET",
        "/api/rest/host_group",
        params={
            "select": "id,name,hosts(id,name)",
            "limit": 2000,
        },
        description=f'localizando Host Group "{name}"'
    )

    obj = find_named(
        r.json(),
        name
    )

    if not obj:
        fail(
            f'Host Group "{name}" não encontrado.'
        )

    ok(
        f'Host Group encontrado: '
        f'{obj["name"]} ({obj["id"]}) - '
        f'{len(obj.get("hosts") or [])} hosts'
    )

    return obj


def get_volume_by_name(
    session,
    base_url,
    auth,
    name
):
    r = ps_request(
        session,
        base_url,
        auth,
        "GET",
        "/api/rest/volume",
        params={
            "select": "id,name,size,state,wwn,nguid",
            "limit": 2000,
        }
    )

    return find_named(
        r.json(),
        name
    )


def get_volume(
    session,
    base_url,
    auth,
    volume_id
):
    r = ps_request(
        session,
        base_url,
        auth,
        "GET",
        f"/api/rest/volume/{volume_id}",
        params={
            "select": "id,name,size,state,wwn,nguid"
        },
        description=f"consultando detalhes do volume {volume_id}"
    )

    return r.json()


def create_volume(
    session,
    base_url,
    auth,
    token,
    storage_name,
    size_gib,
    performance_policy_id,
    app_type
):
    log(
        f'Criando volume PowerStore "{storage_name}" '
        f'com {size_gib:g} GiB...'
    )

    payload = {
        "name": storage_name,
        "size": int(
            size_gib * 1024**3
        ),
        "performance_policy_id":
            performance_policy_id,
        "app_type":
            app_type,
    }

    r = ps_request(
        session,
        base_url,
        auth,
        "POST",
        "/api/rest/volume",
        token=token,
        json_body=payload,
        description="criando volume"
    )

    data = r.json()

    volume_id = data.get(
        "id"
    )

    if not volume_id:
        fail(
            f"PowerStore não retornou ID "
            f"ao criar volume: {data}"
        )

    ok(
        f"Volume criado. ID: {volume_id}"
    )

    return volume_id


def add_volume_to_group(
    session,
    base_url,
    auth,
    token,
    volume_group_id,
    volume_id
):
    log(
        f"Adicionando volume {volume_id} "
        f"ao Volume Group {volume_group_id}..."
    )

    ps_request(
        session,
        base_url,
        auth,
        "POST",
        f"/api/rest/volume_group/"
        f"{volume_group_id}/add_members",
        token=token,
        json_body={
            "volume_ids": [
                volume_id
            ]
        },
        description="adicionando volume ao Volume Group"
    )

    ok("Volume adicionado ao Volume Group")


def get_mapping_for_volume(
    session,
    base_url,
    auth,
    volume_id,
    host_group_id
):
    r = ps_request(
        session,
        base_url,
        auth,
        "GET",
        "/api/rest/host_volume_mapping",
        params={
            "select":
                "id,host_id,host_group_id,"
                "volume_id,logical_unit_number",
            "limit": 2000,
        }
    )

    for m in r.json():

        if (
            m.get("volume_id") == volume_id
            and
            m.get("host_group_id") == host_group_id
        ):
            return m

    return None


def ensure_mapping(
    session,
    base_url,
    auth,
    token,
    volume_id,
    host_group_id
):
    log(
        "Verificando se o Volume Group já "
        "propagou o Host Mapping..."
    )

    mapping = get_mapping_for_volume(
        session,
        base_url,
        auth,
        volume_id,
        host_group_id
    )

    if mapping:
        ok(
            "Mapping já existe. "
            f'LUN/NS: {mapping.get("logical_unit_number")}'
        )
        return mapping

    log(
        "Mapping ainda não existe. "
        "Solicitando attach explícito ao Host Group..."
    )

    ps_request(
        session,
        base_url,
        auth,
        "POST",
        f"/api/rest/volume/{volume_id}/attach",
        token=token,
        json_body={
            "host_group_id":
                host_group_id
        },
        description="criando Host Mapping"
    )

    deadline = (
        time.time() + 40
    )

    attempt = 0

    while time.time() < deadline:

        attempt += 1

        log(
            f"Aguardando mapping aparecer "
            f"(tentativa {attempt})..."
        )

        mapping = get_mapping_for_volume(
            session,
            base_url,
            auth,
            volume_id,
            host_group_id
        )

        if mapping:
            ok(
                "Mapping confirmado. "
                f'LUN/NS: {mapping.get("logical_unit_number")}'
            )
            return mapping

        time.sleep(2)

    fail(
        "Mapping foi solicitado, "
        "mas não apareceu em host_volume_mapping."
    )


# =============================================================================
# VCENTER
# =============================================================================

def connect_vcenter(
    host,
    user,
    password
):
    log(
        f"Conectando ao vCenter {host} como {user}..."
    )

    si = SmartConnect(
        host=host,
        user=user,
        pwd=password,
        sslContext=
            ssl._create_unverified_context(),
    )

    ok("Login vCenter concluído")

    return si


def get_all_hosts(content):
    view = (
        content.viewManager.CreateContainerView(
            content.rootFolder,
            [vim.HostSystem],
            True
        )
    )

    try:
        return list(
            view.view
        )

    finally:
        view.Destroy()


def get_datastore(
    content,
    name
):
    view = (
        content.viewManager.CreateContainerView(
            content.rootFolder,
            [vim.Datastore],
            True
        )
    )

    try:
        for ds in view.view:

            if (
                ds.name.lower()
                == name.lower()
            ):
                return ds

    finally:
        view.Destroy()

    return None


# =============================================================================
# STORAGE FOLDER
# =============================================================================

def get_storage_folder(
    content,
    folder_path
):
    log(
        f'Localizando Storage Folder "{folder_path}"...'
    )

    parts = [
        p.strip()
        for p in
        folder_path.replace("\\", "/").split("/")
        if p.strip()
    ]

    if not parts:
        fail(
            "Storage Folder vazia."
        )

    dc_view = (
        content.viewManager.CreateContainerView(
            content.rootFolder,
            [vim.Datacenter],
            True
        )
    )

    try:
        datacenters = list(
            dc_view.view
        )

    finally:
        dc_view.Destroy()

    candidates = datacenters

    for dc in datacenters:

        if (
            parts
            and
            dc.name.lower()
            == parts[0].lower()
        ):
            candidates = [dc]
            parts = parts[1:]
            break

    if not parts:
        fail(
            "Informe uma Storage Folder "
            "abaixo do Datacenter."
        )

    def walk(
        folder,
        remaining
    ):
        if not remaining:
            return folder

        wanted = (
            remaining[0].lower()
        )

        for child in folder.childEntity:

            if (
                isinstance(child, vim.Folder)
                and
                child.name.lower() == wanted
            ):
                return walk(
                    child,
                    remaining[1:]
                )

        return None

    matches = []

    for dc in candidates:

        found = walk(
            dc.datastoreFolder,
            parts
        )

        if found:
            matches.append(
                (dc, found)
            )

    if not matches:
        fail(
            f'Storage Folder "{folder_path}" '
            f"não encontrada."
        )

    if len(matches) > 1:

        dcs = ", ".join(
            dc.name
            for dc, _ in matches
        )

        fail(
            f'Storage Folder "{folder_path}" '
            f"existe em mais de um Datacenter: {dcs}"
        )

    dc, folder = matches[0]

    ok(
        f"Storage Folder encontrada: "
        f"{dc.name}/{folder_path}"
    )

    return dc, folder


def move_datastore_to_folder(
    content,
    datastore,
    folder_path
):
    dc, folder = (
        get_storage_folder_cached(
            content,
            folder_path
        )
    )

    if datastore.parent == folder:
        log(
            f'Datastore "{datastore.name}" '
            f"já está na Storage Folder correta."
        )
        return dc, folder

    log(
        f'Movendo datastore "{datastore.name}" '
        f'para Storage Folder "{folder_path}"...'
    )

    task = folder.MoveIntoFolder_Task(
        list=[
            datastore
        ]
    )

    counter = 0

    while task.info.state in (
        vim.TaskInfo.State.queued,
        vim.TaskInfo.State.running
    ):

        counter += 1

        if counter % 3 == 0:
            log(
                "Aguardando conclusão da movimentação "
                "do datastore..."
            )

        time.sleep(1)

    if (
        task.info.state
        != vim.TaskInfo.State.success
    ):
        fail(
            f'Falha ao mover datastore '
            f'"{datastore.name}" para '
            f'Storage Folder "{folder_path}": '
            f"{task.info.error}"
        )

    ok(
        f'Datastore movido para "{folder_path}"'
    )

    return dc, folder


# =============================================================================
# CACHE
# =============================================================================

CACHE = {
    "volume_groups": {},
    "host_groups": {},
    "storage_folders": {},
}


def get_volume_group_cached(
    session,
    base_url,
    auth,
    name
):
    key = name.lower()

    if key not in CACHE["volume_groups"]:
        CACHE["volume_groups"][key] = get_volume_group(
            session,
            base_url,
            auth,
            name
        )
    else:
        log(
            f'Usando Volume Group "{name}" do cache.'
        )

    return CACHE["volume_groups"][key]


def get_host_group_cached(
    session,
    base_url,
    auth,
    name
):
    key = name.lower()

    if key not in CACHE["host_groups"]:
        CACHE["host_groups"][key] = get_host_group(
            session,
            base_url,
            auth,
            name
        )
    else:
        log(
            f'Usando Host Group "{name}" do cache.'
        )

    return CACHE["host_groups"][key]


CURRENT_CONTENT = None


def get_storage_folder_cached(
    content,
    folder_path
):
    key = folder_path.lower()

    if key not in CACHE["storage_folders"]:
        CACHE["storage_folders"][key] = get_storage_folder(
            content,
            folder_path
        )
    else:
        log(
            f'Usando Storage Folder "{folder_path}" do cache.'
        )

    return CACHE["storage_folders"][key]


# =============================================================================
# HOSTS / RESCAN
# =============================================================================

def select_hosts_for_host_group(
    content,
    ps_host_group
):
    vc_hosts = get_all_hosts(
        content
    )

    by_short = {
        normalize_host(h.name): h
        for h in vc_hosts
    }

    selected = []
    missing = []

    for h in (
        ps_host_group.get("hosts")
        or []
    ):
        short = normalize_host(
            h.get("name")
        )

        if short in by_short:
            selected.append(
                by_short[short]
            )
        else:
            missing.append(
                h.get("name")
            )

    if missing:
        fail(
            "Hosts do Host Group não encontrados "
            "no vCenter: "
            + ", ".join(
                str(x)
                for x in missing
            )
        )

    if not selected:
        fail(
            "Nenhum host do Host Group "
            "foi localizado no vCenter."
        )

    selected = sorted(
        selected,
        key=lambda h: h.name.lower()
    )

    log(
        f"{len(selected)} hosts do Host Group "
        f"localizados no vCenter."
    )

    return selected


def rescan_host(
    host,
    hba=True,
    vmfs=True
):
    log(
        f"Rescan em {host.name} "
        f"(HBA={hba}, VMFS={vmfs})..."
    )

    if hba:
        host.configManager.storageSystem.RescanAllHba()

    if vmfs:
        host.configManager.storageSystem.RescanVmfs()

    ok(
        f"Rescan concluído em {host.name}"
    )


def rescan_all_hosts(
    hosts,
    description="Rescan geral"
):
    title(
        f"{description} - {len(hosts)} hosts"
    )

    for index, host in enumerate(
        hosts,
        1
    ):
        log(
            f"[{index}/{len(hosts)}] "
            f"Rescan {host.name}"
        )

        try:
            host.configManager.storageSystem.RescanAllHba()
            host.configManager.storageSystem.RescanVmfs()

            ok(
                f"{host.name}"
            )

        except Exception as e:
            warn(
                f"Erro no rescan de {host.name}: {e}"
            )


def find_device_on_host(
    host,
    device_name
):
    storage = (
        host.config.storageDevice
    )

    if not storage:
        return None

    for lun in storage.scsiLun:

        if (
            getattr(
                lun,
                "canonicalName",
                None
            )
            == device_name
        ):
            return lun

    return None


def wait_for_device_on_host(
    host,
    device_name,
    timeout_seconds
):
    log(
        f"Aguardando device {device_name} "
        f"aparecer em {host.name}..."
    )

    deadline = (
        time.time()
        + timeout_seconds
    )

    attempt = 0

    while time.time() < deadline:

        attempt += 1

        lun = find_device_on_host(
            host,
            device_name
        )

        if lun:
            ok(
                f"Device encontrado em {host.name} "
                f"na tentativa {attempt}"
            )
            return lun

        remaining = int(
            deadline - time.time()
        )

        log(
            f"Device ainda não visível em {host.name}. "
            f"Tentativa {attempt}. "
            f"Restam aproximadamente {remaining}s."
        )

        log(
            f"Executando novo rescan HBA em {host.name}..."
        )

        host.configManager.storageSystem.RescanAllHba()

        time.sleep(
            POLL_SECONDS
        )

    return None


# =============================================================================
# VMFS
# =============================================================================

def create_vmfs_datastore(
    content,
    creation_host,
    datastore_name,
    eui,
    expected_size_gib
):
    log(
        f'Preparando criação do datastore '
        f'"{datastore_name}" em {creation_host.name}.'
    )

    existing = get_datastore(
        content,
        datastore_name
    )

    if existing:
        fail(
            f'Datastore "{datastore_name}" '
            f"já existe."
        )

    lun = wait_for_device_on_host(
        creation_host,
        eui,
        WAIT_DEVICE_SECONDS
    )

    if not lun:
        fail(
            f"Device {eui} não apareceu "
            f"em {creation_host.name} dentro do timeout."
        )

    capacity_gib = (
        lun.capacity.block
        * lun.capacity.blockSize
        / 1024**3
    )

    log(
        f"Device encontrado: {eui}"
    )

    log(
        f"Capacidade detectada: "
        f"{capacity_gib:.2f} GiB"
    )

    log(
        f"Capacidade esperada: "
        f"{expected_size_gib:g} GiB"
    )

    if not (
        expected_size_gib * 0.95
        <= capacity_gib
        <= expected_size_gib * 1.05
    ):
        fail(
            f"Capacidade do device não confere. "
            f"Esperado ~{expected_size_gib} GiB, "
            f"detectado {capacity_gib:.2f} GiB."
        )

    ok(
        "Capacidade validada"
    )

    ds_system = (
        creation_host
        .configManager
        .datastoreSystem
    )

    device_path = (
        f"/vmfs/devices/disks/{eui}"
    )

    log(
        f"Consultando opções de criação VMFS6 "
        f"para {device_path}..."
    )

    options = (
        ds_system
        .QueryVmfsDatastoreCreateOptions(
            devicePath=device_path,
            vmfsMajorVersion=6,
        )
    )

    if not options:
        fail(
            f"vSphere não retornou opções "
            f"de criação VMFS para {eui}."
        )

    log(
        f"vSphere retornou {len(options)} "
        f"opção(ões) de criação."
    )

    spec = (
        options[0].spec
    )

    if not spec.vmfs:
        fail(
            "Spec retornada pelo vSphere "
            "não possui configuração VMFS."
        )

    spec.vmfs.volumeName = (
        datastore_name
    )

    log(
        f'Criando VMFS6 "{datastore_name}"...'
    )

    ds = (
        ds_system
        .CreateVmfsDatastore(
            spec=spec
        )
    )

    ok(
        f'Datastore "{datastore_name}" criado'
    )

    return ds


def validate_datastore(
    content,
    datastore_name,
    expected_eui
):
    log(
        f'Validando datastore "{datastore_name}" '
        f"no inventário do vCenter..."
    )

    deadline = (
        time.time() + 60
    )

    attempt = 0

    while time.time() < deadline:

        attempt += 1

        ds = get_datastore(
            content,
            datastore_name
        )

        if ds:

            info = ds.info

            if isinstance(
                info,
                vim.host.VmfsDatastoreInfo
            ):

                devices = [
                    x.diskName
                    for x in info.vmfs.extent
                ]

                if expected_eui not in devices:
                    fail(
                        f'Datastore "{datastore_name}" existe, '
                        f"mas não usa o device esperado "
                        f"{expected_eui}."
                    )

            ok(
                f'Datastore "{datastore_name}" '
                f"validado no vCenter"
            )

            return ds

        log(
            f"Datastore ainda não apareceu "
            f"(tentativa {attempt})..."
        )

        time.sleep(3)

    fail(
        f'Datastore "{datastore_name}" '
        f"não apareceu no inventário do vCenter."
    )


# =============================================================================
# BATCH
# =============================================================================

def load_batch(path):

    log(
        f"Carregando arquivo batch: {path}"
    )

    p = Path(
        path
    )

    if not p.exists():
        fail(
            f"Arquivo não encontrado: {path}"
        )

    suffix = (
        p.suffix.lower()
    )

    if suffix == ".json":

        with p.open(
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        if isinstance(
            data,
            dict
        ):
            data = data.get(
                "volumes"
            )

        if not isinstance(
            data,
            list
        ):
            fail(
                'JSON deve conter uma lista em "volumes".'
            )

        rows = []

        for row in data:

            legacy_name = (
                row.get("name")
            )

            storage_name = (
                row.get("storage_name")
                or legacy_name
            )

            datastore_name = (
                row.get("datastore_name")
                or legacy_name
            )

            if not storage_name:
                fail(
                    "Entrada JSON sem storage_name."
                )

            if not datastore_name:
                fail(
                    "Entrada JSON sem datastore_name."
                )

            rows.append({
                "storage_name":
                    str(storage_name),

                "datastore_name":
                    str(datastore_name),

                "size":
                    float(row["size"]),

                "volume_group":
                    row.get("volume_group"),

                "host_group":
                    row.get("host_group"),
            })

        ok(
            f"{len(rows)} volumes carregados do JSON"
        )

        return rows


    if suffix == ".csv":

        rows = []

        with p.open(
            newline="",
            encoding="utf-8-sig"
        ) as f:

            reader = csv.DictReader(
                f
            )

            for row in reader:

                legacy_name = (
                    row.get("name")
                )

                storage_name = (
                    row.get("storage_name")
                    or legacy_name
                )

                datastore_name = (
                    row.get("datastore_name")
                    or legacy_name
                )

                if not storage_name:
                    fail(
                        "CSV sem storage_name."
                    )

                if not datastore_name:
                    fail(
                        "CSV sem datastore_name."
                    )

                rows.append({
                    "storage_name":
                        storage_name.strip(),

                    "datastore_name":
                        datastore_name.strip(),

                    "size":
                        float(row["size"]),

                    "volume_group":
                        (
                            row.get("volume_group")
                            or ""
                        ).strip() or None,

                    "host_group":
                        (
                            row.get("host_group")
                            or ""
                        ).strip() or None,
                })

        ok(
            f"{len(rows)} volumes carregados do CSV"
        )

        return rows


    fail(
        "Formato suportado: .json ou .csv"
    )


# =============================================================================
# PROVISIONAMENTO
# =============================================================================

def provision_one(
    ctx,
    storage_name,
    datastore_name,
    size_gib,
    volume_group_name,
    host_group_name,
    storage_folder,
    batch_mode
):
    title(
        f"PROVISIONANDO\n"
        f"PowerStore : {storage_name}\n"
        f"vCenter    : {datastore_name}\n"
        f"Tamanho    : {size_gib:g} GiB"
    )

    session = (
        ctx["ps_session"]
    )

    auth = (
        ctx["ps_auth"]
    )

    token = (
        ctx["ps_token"]
    )

    ps_url = (
        ctx["powerstore"]
    )

    content = (
        ctx["vc_content"]
    )

    log(
        "Etapa 1/10 - Verificando conflito de nomes..."
    )

    if get_volume_by_name(
        session,
        ps_url,
        auth,
        storage_name
    ):
        fail(
            f'Volume PowerStore "{storage_name}" '
            f"já existe."
        )

    if get_datastore(
        content,
        datastore_name
    ):
        fail(
            f'Datastore vCenter "{datastore_name}" '
            f"já existe."
        )

    ok(
        "Não existem conflitos de nome"
    )


    log(
        "Etapa 2/10 - Resolvendo grupos e destino..."
    )

    vg = get_volume_group_cached(
        session,
        ps_url,
        auth,
        volume_group_name
    )

    hg = get_host_group_cached(
        session,
        ps_url,
        auth,
        host_group_name
    )

    target_hosts = (
        select_hosts_for_host_group(
            content,
            hg
        )
    )

    get_storage_folder_cached(
        content,
        storage_folder
    )

    creation_host = (
        target_hosts[0]
    )

    log(
        f"Host utilizado para criação: "
        f"{creation_host.name}"
    )

    if batch_mode:
        log(
            "Modo batch: apenas este host receberá "
            "rescan durante a criação deste datastore."
        )
    else:
        log(
            "Modo pontual: ao final haverá rescan "
            "em todos os hosts do Host Group."
        )


    log(
        "Etapa 3/10 - Criando volume no PowerStore..."
    )

    volume_id = create_volume(
        session,
        ps_url,
        auth,
        token,
        storage_name,
        size_gib,
        ctx["performance_policy_id"],
        ctx["app_type"],
    )


    log(
        "Etapa 4/10 - Adicionando ao Volume Group..."
    )

    add_volume_to_group(
        session,
        ps_url,
        auth,
        token,
        vg["id"],
        volume_id
    )


    log(
        "Etapa 5/10 - Validando Host Mapping..."
    )

    mapping = ensure_mapping(
        session,
        ps_url,
        auth,
        token,
        volume_id,
        hg["id"]
    )


    log(
        f'Mapping ID: {mapping.get("id")}'
    )

    log(
        f'LUN/NS: {mapping.get("logical_unit_number")}'
    )


    log(
        "Etapa 6/10 - Obtendo NGUID/EUI..."
    )

    volume = get_volume(
        session,
        ps_url,
        auth,
        volume_id
    )

    nguid = (
        volume.get("nguid")
    )

    if (
        not nguid
        or
        not nguid.startswith("nguid.")
    ):
        fail(
            f"NGUID inesperado no volume "
            f"{storage_name}: {nguid}"
        )

    eui = (
        "eui."
        + nguid[len("nguid."):]
    )

    log(
        f"NGUID : {nguid}"
    )

    log(
        f"EUI   : {eui}"
    )


    log(
        "Etapa 7/10 - Rescan do host de criação..."
    )

    rescan_host(
        creation_host,
        hba=True,
        vmfs=True
    )


    log(
        "Etapa 8/10 - Criando VMFS6..."
    )

    ds = create_vmfs_datastore(
        content,
        creation_host,
        datastore_name,
        eui,
        size_gib
    )


    log(
        "Etapa 9/10 - Validando datastore..."
    )

    ds = validate_datastore(
        content,
        datastore_name,
        eui
    )


    log(
        "Etapa 10/10 - Movendo para Storage Folder..."
    )

    dc, folder = (
        move_datastore_to_folder(
            content,
            ds,
            storage_folder
        )
    )


    if not batch_mode:

        title(
            "RESCAN FINAL DO PROVISIONAMENTO PONTUAL"
        )

        rescan_all_hosts(
            target_hosts,
            description=
                f'Rescan final de "{datastore_name}"'
        )


    info = (
        ds.info
    )

    vmfs_uuid = (
        info.vmfs.uuid
        if isinstance(
            info,
            vim.host.VmfsDatastoreInfo
        )
        else None
    )

    capacity_gib = (
        ds.summary.capacity
        / 1024**3
    )

    free_gib = (
        ds.summary.freeSpace
        / 1024**3
    )

    result = {
        "storage_name":
            storage_name,

        "datastore_name":
            datastore_name,

        "size_gib_requested":
            size_gib,

        "volume_id":
            volume_id,

        "volume_group":
            volume_group_name,

        "host_group":
            host_group_name,

        "storage_folder":
            storage_folder,

        "nguid":
            nguid,

        "eui":
            eui,

        "vmfs_uuid":
            vmfs_uuid,

        "capacity_gib":
            round(
                capacity_gib,
                2
            ),

        "free_gib":
            round(
                free_gib,
                2
            ),

        "status":
            "OK",
    }

    title(
        "PROVISIONAMENTO CONCLUÍDO"
    )

    ok(
        f'{storage_name} -> {datastore_name}'
    )

    log(
        f"Volume ID  : {volume_id}"
    )

    log(
        f"EUI        : {eui}"
    )

    log(
        f"VMFS UUID  : {vmfs_uuid}"
    )

    log(
        f"Capacidade : {capacity_gib:.2f} GiB"
    )

    log(
        f"Free       : {free_gib:.2f} GiB"
    )

    return result, target_hosts


# =============================================================================
# VALIDAÇÃO FINAL DO BATCH
# =============================================================================

def final_batch_rescan_and_validate(
    content,
    results,
    all_target_hosts
):
    title(
        "BATCH FINALIZADO - RESCAN GERAL DOS HOSTS"
    )

    unique_hosts = {}

    for host in all_target_hosts:
        unique_hosts[host.name.lower()] = host

    hosts = sorted(
        unique_hosts.values(),
        key=lambda h: h.name.lower()
    )

    log(
        f"Serão atualizados {len(hosts)} hosts."
    )

    rescan_all_hosts(
        hosts,
        description="Rescan final do batch"
    )

    title(
        "VALIDAÇÃO FINAL DOS DATASTORES"
    )

    time.sleep(3)

    ok_count = 0

    for index, result in enumerate(
        results,
        1
    ):

        if result.get("status") != "OK":
            continue

        datastore_name = (
            result["datastore_name"]
        )

        expected_eui = (
            result["eui"]
        )

        log(
            f"[{index}/{len(results)}] "
            f'Validando "{datastore_name}"...'
        )

        ds = get_datastore(
            content,
            datastore_name
        )

        if not ds:
            warn(
                f'Datastore "{datastore_name}" '
                f"não foi encontrado."
            )
            result["final_validation"] = "NOT_FOUND"
            continue

        info = ds.info

        if isinstance(
            info,
            vim.host.VmfsDatastoreInfo
        ):

            extents = [
                e.diskName
                for e in info.vmfs.extent
            ]

            if expected_eui not in extents:

                warn(
                    f'"{datastore_name}" não usa '
                    f"o EUI esperado."
                )

                result["final_validation"] = "WRONG_EUI"

                continue

        mounted_hosts = []

        try:
            for mount in ds.host:

                if (
                    mount.mountInfo
                    and
                    mount.mountInfo.mounted
                ):
                    mounted_hosts.append(
                        mount.key.name
                    )

        except Exception:
            pass

        result["mounted_hosts"] = (
            len(mounted_hosts)
        )

        result["final_validation"] = "OK"

        ok_count += 1

        ok(
            f'{datastore_name} - '
            f'{len(mounted_hosts)} hosts montados'
        )

    log()

    log(
        f"Validação final concluída: "
        f"{ok_count} datastore(s) OK."
    )


# =============================================================================
# ARGUMENTOS
# =============================================================================

def parse_args():

    p = argparse.ArgumentParser(
        description=(
            "Provisiona volumes Dell PowerStore "
            "e cria datastores VMFS6 no vCenter."
        )
    )

    p.add_argument(
        "--batch",
        help="Arquivo CSV ou JSON para execução em lote."
    )

    p.add_argument(
        "--storage-name",
        help="Nome do volume no PowerStore."
    )

    p.add_argument(
        "--datastore-name",
        help="Nome do datastore no vCenter."
    )

    p.add_argument(
        "--size",
        type=float,
        help="Tamanho em GiB para criação pontual."
    )

    p.add_argument(
        "--volume-group",
        required=True,
        help="Volume Group padrão no PowerStore."
    )

    p.add_argument(
        "--host-group",
        required=True,
        help="Host Group padrão no PowerStore."
    )

    p.add_argument(
        "--storage-folder",
        required=True,
        help="Storage Folder destino no vCenter."
    )

    p.add_argument(
        "--powerstore",
        required=True
    )

    p.add_argument(
        "--powerstore-user",
        required=True
    )

    p.add_argument(
        "--vcenter",
        required=True
    )

    p.add_argument(
        "--vcenter-user",
        required=True
    )

    p.add_argument(
        "--performance-policy-id",
        default=
            DEFAULT_PERFORMANCE_POLICY_ID
    )

    p.add_argument(
        "--app-type",
        default=
            DEFAULT_APP_TYPE
    )

    p.add_argument(
        "--continue-on-error",
        action="store_true"
    )

    p.add_argument(
        "--report",
        help="Arquivo JSON com relatório da execução."
    )

    args = p.parse_args()

    if args.batch:

        if (
            args.storage_name
            or
            args.datastore_name
            or
            args.size is not None
        ):
            p.error(
                "Com --batch não use "
                "--storage-name, --datastore-name ou --size."
            )

    else:

        if not args.storage_name:
            p.error(
                "--storage-name é obrigatório "
                "na criação pontual."
            )

        if not args.datastore_name:
            p.error(
                "--datastore-name é obrigatório "
                "na criação pontual."
            )

        if args.size is None:
            p.error(
                "--size é obrigatório "
                "na criação pontual."
            )

    return args


# =============================================================================
# MAIN
# =============================================================================

def main():

    args = parse_args()

    batch_mode = bool(
        args.batch
    )

    title(
        "STO2VCENTER"
    )

    if batch_mode:
        log(
            "Modo de execução: BATCH"
        )
    else:
        log(
            "Modo de execução: PONTUAL"
        )

    log(
        f"PowerStore     : {args.powerstore}"
    )

    log(
        f"vCenter        : {args.vcenter}"
    )

    log(
        f"Volume Group   : {args.volume_group}"
    )

    log(
        f"Host Group     : {args.host_group}"
    )

    log(
        f"Storage Folder : {args.storage_folder}"
    )

    ps_password = getpass.getpass(
        "Senha PowerStore: "
    )

    vc_password = getpass.getpass(
        "Senha vCenter: "
    )

    title(
        "LOGIN POWERSTORE"
    )

    ps_session, ps_auth, ps_token = (
        powerstore_login(
            args.powerstore,
            args.powerstore_user,
            ps_password
        )
    )

    title(
        "LOGIN VCENTER"
    )

    si = connect_vcenter(
        args.vcenter,
        args.vcenter_user,
        vc_password
    )

    try:

        content = (
            si.RetrieveContent()
        )

        ctx = {
            "ps_session":
                ps_session,

            "ps_auth":
                ps_auth,

            "ps_token":
                ps_token,

            "powerstore":
                args.powerstore,

            "vc_content":
                content,

            "performance_policy_id":
                args.performance_policy_id,

            "app_type":
                args.app_type,
        }

        if batch_mode:

            jobs = load_batch(
                args.batch
            )

        else:

            jobs = [{
                "storage_name":
                    args.storage_name,

                "datastore_name":
                    args.datastore_name,

                "size":
                    args.size,

                "volume_group":
                    None,

                "host_group":
                    None,
            }]

        if not jobs:
            fail(
                "Nenhum volume para processar."
            )

        title(
            "INÍCIO DO PROCESSAMENTO"
        )

        log(
            f"Total de itens: {len(jobs)}"
        )

        results = []

        all_target_hosts = []

        for i, job in enumerate(
            jobs,
            1
        ):

            storage_name = (
                job["storage_name"]
            )

            datastore_name = (
                job["datastore_name"]
            )

            size = float(
                job["size"]
            )

            vg = (
                job.get("volume_group")
                or
                args.volume_group
            )

            hg = (
                job.get("host_group")
                or
                args.host_group
            )

            title(
                f"ITEM {i}/{len(jobs)}"
            )

            log(
                f"PowerStore : {storage_name}"
            )

            log(
                f"vCenter    : {datastore_name}"
            )

            log(
                f"Tamanho    : {size:g} GiB"
            )

            start_time = time.time()

            try:

                result, target_hosts = provision_one(
                    ctx,
                    storage_name,
                    datastore_name,
                    size,
                    vg,
                    hg,
                    args.storage_folder,
                    batch_mode
                )

                all_target_hosts.extend(
                    target_hosts
                )

                duration = (
                    time.time()
                    - start_time
                )

                result["duration_seconds"] = round(
                    duration,
                    1
                )

                results.append(
                    result
                )

                ok(
                    f"Item {i}/{len(jobs)} concluído "
                    f"em {duration:.1f}s"
                )

            except Exception as e:

                duration = (
                    time.time()
                    - start_time
                )

                result = {
                    "storage_name":
                        storage_name,

                    "datastore_name":
                        datastore_name,

                    "size_gib_requested":
                        size,

                    "volume_group":
                        vg,

                    "host_group":
                        hg,

                    "storage_folder":
                        args.storage_folder,

                    "status":
                        "ERROR",

                    "error":
                        str(e),

                    "duration_seconds":
                        round(duration, 1),
                }

                results.append(
                    result
                )

                title(
                    f"ERRO NO ITEM {i}/{len(jobs)}"
                )

                log(
                    f"{storage_name} -> "
                    f"{datastore_name}"
                )

                log(
                    f"Erro: {e}"
                )

                if not args.continue_on_error:
                    warn(
                        "Execução interrompida porque "
                        "--continue-on-error não foi informado."
                    )
                    break

                warn(
                    "Continuando para o próximo item."
                )

        if batch_mode and all_target_hosts:

            final_batch_rescan_and_validate(
                content,
                results,
                all_target_hosts
            )

        title(
            "RESUMO"
        )

        success_count = 0
        error_count = 0

        for r in results:

            if r["status"] == "OK":
                success_count += 1
            else:
                error_count += 1

            log(
                f"{r['status']:<5}  "
                f"{r['storage_name']} -> "
                f"{r['datastore_name']} "
                f"({r.get('duration_seconds', 0)}s)"
            )

        log()

        log(
            f"Total processado : {len(results)}"
        )

        log(
            f"Sucesso          : {success_count}"
        )

        log(
            f"Erros            : {error_count}"
        )

        if args.report:

            log(
                f"Gravando relatório em "
                f"{args.report}..."
            )

            Path(
                args.report
            ).write_text(
                json.dumps(
                    results,
                    indent=2,
                    ensure_ascii=False
                ),
                encoding="utf-8",
            )

            ok(
                f"Relatório salvo: {args.report}"
            )

        if any(
            r["status"] != "OK"
            for r in results
        ):
            sys.exit(2)

    finally:

        log(
            "Encerrando sessão do vCenter..."
        )

        Disconnect(
            si
        )

        log(
            "Fim."
        )


if __name__ == "__main__":
    main()


chmod +x sto2vcenter.py
