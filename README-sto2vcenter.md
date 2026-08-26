# sto2vcenter.py

Automação para provisionamento de volumes em storage Dell PowerStore e criação automática dos respectivos datastores VMFS6 no VMware vCenter.

O script suporta criação pontual e criação em lote, realizando toda a cadeia:

```text
PowerStore
  ↓
Criação do volume
  ↓
Associação ao Volume Group
  ↓
Mapeamento ao Host Group
  ↓
Obtenção do NGUID
  ↓
Conversão NGUID → EUI
  ↓
Rescan no ESXi
  ↓
Criação do VMFS6
  ↓
Criação do datastore
  ↓
Movimentação para Storage Folder
  ↓
Validação
```

## Requisitos

O script foi desenvolvido e testado com:

```text
Python:         3.11
PowerStoreOS:   4.3.0.0
VMware:         vCenter / ESXi
Storage:        Dell PowerStore
Protocolo:      NVMe
Filesystem:     VMFS6
```

Bibliotecas Python necessárias:

```bash
/bin/python -m pip install requests pyvmomi
```

O interpretador esperado no script é:

```bash
/bin/python
```

## Permissões necessárias

A conta utilizada no PowerStore precisa possuir permissões para:

* consultar volumes;
* criar volumes;
* consultar Volume Groups;
* adicionar volumes a Volume Groups;
* consultar Host Groups;
* criar Host Mappings.

A conta utilizada no vCenter precisa possuir permissões para:

* consultar hosts e datastores;
* executar rescan de storage;
* consultar devices;
* criar VMFS;
* criar datastores;
* mover datastores entre Storage Folders.

## Conceitos utilizados

### Volume Group

Agrupamento lógico de volumes no PowerStore.

Exemplo:

```text
My Volume Group
```

### Host Group

Agrupamento dos hosts ESXi que terão acesso aos volumes.

Exemplo:

```text
My Host Group
```

### Storage Folder

Pasta lógica do inventário do vCenter onde o datastore será organizado.

Exemplo:

```text
MyFolder
```

Também é possível indicar caminhos:

```text
Web Servers RedHat/HA
```

Caso existam Storage Folders com o mesmo nome em mais de um Datacenter, informe o Datacenter:

```text
Datacenter X/MyFolder
```

## Correlação PowerStore x VMware

No ambiente NVMe, o PowerStore informa o identificador do volume como NGUID.

Exemplo:

```text
nguid.be15e1db0e25d78e8ccf09680072ce81
```

O VMware apresenta o mesmo device como EUI:

```text
eui.be15e1db0e25d78e8ccf09680072ce81
```

O script realiza automaticamente a conversão:

```python
eui = "eui." + nguid[len("nguid."):]
```

Essa identificação é utilizada para garantir que o VMFS seja criado exatamente no volume recém-provisionado.

O script não procura simplesmente por "um disco novo".

## Instalação

Copie o script para o servidor:

```bash
chmod +x sto2vcenter.py
```

Valide a sintaxe:

```bash
/bin/python -m py_compile sto2vcenter.py
```

Resultado esperado:

```text
nenhuma saída
```

Ou:

```bash
/bin/python -m py_compile sto2vcenter.py && echo "SCRIPT OK"
```

## Ajuda

```bash
./sto2vcenter.py --help
```

## Criação pontual

Para criar apenas um volume/datastore:

```bash
./sto2vcenter.py \
  --storage-name "Storage Name" \
  --datastore-name "Datastore Name" \
  --size 500 \
  --volume-group "My Volume Group" \
  --host-group "My Host Group" \
  --storage-folder "MyFolder" \
  --powerstore "https://powerstore.contoso.com" \
  --powerstore-user "user" \
  --vcenter "vcenter.contoso.com" \
  --vcenter-user "user@contoso.com"
```

O parâmetro `--size` é informado em GiB.

As senhas não são passadas na linha de comando.

O script solicitará:

```text
Senha PowerStore:
Senha vCenter:
```

## Parâmetros

### `--storage-name`

Nome do volume no Dell PowerStore.

Exemplo:

```bash
--storage-name "Storage Name"
```

### `--datastore-name`

Nome do datastore no VMware vCenter.

Exemplo:

```bash
--datastore-name "Datastore Name"
```

### `--size`

Tamanho do volume em GiB.

Exemplo:

```bash
--size 500
```

### `--volume-group`

Volume Group onde o novo volume será inserido.

Exemplo:

```bash
--volume-group "My Volume Group"
```

### `--host-group`

Host Group que receberá acesso ao volume.

Exemplo:

```bash
--host-group "My Host Group"
```

### `--storage-folder`

Storage Folder do inventário do vCenter para onde o datastore será movido.

Exemplo:

```bash
--storage-folder "MyFolder"
```

### `--powerstore`

URL de gerenciamento do Dell PowerStore.

Exemplo:

```bash
--powerstore "https://powerstore.contoso.com"
```

### `--powerstore-user`

Usuário utilizado na API REST do PowerStore.

### `--vcenter`

FQDN do vCenter.

### `--vcenter-user`

Usuário utilizado para conexão ao vCenter.

### `--performance-policy-id`

Opcional.

Valor padrão:

```text
default_high
```

### `--app-type`

Opcional.

Valor padrão:

```text
Virtualization_Virtual_Servers_VSI
```

### `--continue-on-error`

Utilizado principalmente no modo batch.

Faz com que o processamento continue caso um dos volumes apresente erro.

### `--report`

Arquivo JSON onde será gravado o resultado da execução.

Exemplo:

```bash
--report resultado.json
```

## Execução em batch

O script aceita arquivos JSON e CSV.

Para um conjunto grande de volumes, JSON é o formato recomendado.

Exemplo:

```json
{
  "volumes": [
    {
      "storage_name": "Storage Name 01",
      "datastore_name": "Datastore Name 01",
      "size": 512
    },
    {
      "storage_name": "Storage Name 02",
      "datastore_name": "Datastore Name 02",
      "size": 832
    },
    {
      "storage_name": "Storage Name 03",
      "datastore_name": "Datastore Name 03",
      "size": 832
    }
  ]
}
```

Execute:

```bash
./sto2vcenter.py \
  --batch volumes.json \
  --volume-group "My Volume Group" \
  --host-group "My Host Group" \
  --storage-folder "Myfolder" \
  --powerstore "https://powerstore.contoso.com" \
  --powerstore-user "user" \
  --vcenter "vcenter.contoso.com" \
  --vcenter-user "user@contoso.com" \
  --continue-on-error \
  --report resultado.json
```

O Volume Group, Host Group e Storage Folder informados na linha de comando serão utilizados para todos os itens do batch.

## Volume Group e Host Group por volume

Opcionalmente, um item do batch pode sobrescrever o Volume Group e o Host Group padrão:

```json
{
  "volumes": [
    {
      "storage_name": "Storage Name",
      "datastore_name": "Datastore Name",
      "size": 1024,
      "volume_group": "My Volume Group",
      "host_group": "My Host Group"
    }
  ]
}
```

Caso esses campos não existam, serão utilizados os valores da linha de comando.

## Compatibilidade com JSON antigo

O script mantém compatibilidade com arquivos que possuam apenas:

```json
{
  "name": "Nome do volume",
  "size": 500
}
```

Nesse caso, o mesmo nome será utilizado no PowerStore e no vCenter.

Para novos arquivos deve-se utilizar:

```text
storage_name
datastore_name
```

## Fluxo de provisionamento

Para cada volume o script executa:

```text
1. Verifica se já existe volume com o mesmo nome no PowerStore.

2. Verifica se já existe datastore com o mesmo nome no vCenter.

3. Localiza o Volume Group.

4. Localiza o Host Group.

5. Confirma os hosts pertencentes ao Host Group.

6. Valida a Storage Folder.

7. Cria o volume no PowerStore.

8. Adiciona o volume ao Volume Group.

9. Confirma ou cria o Host Mapping.

10. Consulta o volume recém-criado.

11. Obtém o NGUID.

12. Converte NGUID para EUI.

13. Executa rescan no ESXi.

14. Aguarda o device NVMe aparecer.

15. Confirma o tamanho do device.

16. Consulta as opções VMFS6 recomendadas pelo vSphere.

17. Cria o VMFS6.

18. Cria o datastore com o nome solicitado.

19. Valida se o datastore utiliza o EUI esperado.

20. Move o datastore para a Storage Folder.

21. Realiza validações finais.
```

## Otimização de rescan em batch

No modo pontual, o provisionamento pode terminar com rescan dos hosts pertencentes ao Host Group.

No modo batch, o comportamento foi otimizado.

Durante a criação de cada datastore:

```text
PowerStore
    ↓
cria volume
    ↓
mapping
    ↓
apenas primeiro ESXi
    ↓
rescan
    ↓
device aparece
    ↓
VMFS criado
```

Não é realizado rescan nos 17 hosts a cada volume.

Após o último item do batch:

```text
último volume
    ↓
rescan geral
    ↓
ESXi 1
ESXi 2
ESXi 3
...
ESXi N
    ↓
validação de todos os datastores
```

Isso reduz significativamente o número de operações de storage discovery no cluster.

## Seleção do host de criação

Os hosts pertencentes ao Host Group são correlacionados com os hosts existentes no vCenter.

O script ordena os hosts e utiliza o primeiro deles para criação dos datastores no modo batch.

Exemplo:

```text
ESXISERVER01
ESXISERVER02
...
ESXISERVER55
```

Nesse caso:

```text
ESXISERVER55
```

será normalmente utilizado para:

```text
rescan inicial
detecção do EUI
criação do VMFS
```

Ao final do batch todos os hosts recebem rescan.

## Timeout para descoberta do device

Por padrão:

```text
WAIT_DEVICE_SECONDS = 240
```

Ou seja, o script aguarda até 4 minutos para o namespace NVMe aparecer no host de criação.

A consulta é repetida a cada:

```text
POLL_SECONDS = 5
```

Durante esse período novos rescans são executados no host de criação.

## Validações de segurança

Antes de criar o VMFS, o script verifica:

### Nome do volume

Não pode existir outro volume com o mesmo nome no PowerStore.

### Nome do datastore

Não pode existir outro datastore com o mesmo nome no vCenter.

### Storage Folder

A pasta precisa existir antes da criação do volume.

### EUI

O device utilizado para VMFS precisa ser exatamente o EUI derivado do NGUID do volume recém-criado.

Exemplo:

```text
PowerStore:

nguid.1234567890abcdef

VMware:

eui.1234567890abcdef
```

### Tamanho

O tamanho detectado pelo ESXi precisa estar dentro de aproximadamente 5% do tamanho solicitado.

Essa validação evita criar VMFS em um device incorreto.

## Performance Policy

Por padrão:

```text
default_high
```

Equivale à política:

```text
High
```

no PowerStore.

Pode ser sobrescrita:

```bash
--performance-policy-id OUTRO_ID
```

## Application Type

Por padrão:

```text
Virtualization_Virtual_Servers_VSI
```

Correspondente à configuração:

```text
Category:
Virtualization

Application:
Virtual Servers (VSI)
```

## Relatório de execução

Quando utilizado:

```bash
--report resultado.json
```

o script grava informações como:

```json
{
  "storage_name": "Storage Name",
  "datastore_name": "Datastore Name",
  "size_gib_requested": 512,
  "volume_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "volume_group": "My Volume Group",
  "host_group": "My Host Group",
  "storage_folder": "MyFolder",
  "nguid": "nguid.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "eui": "eui.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "vmfs_uuid": "xxxxxxxx-xxxxxxxx-xxxx-xxxxxxxxxxxx",
  "capacity_gib": 511.75,
  "free_gib": 510.0,
  "status": "OK",
  "duration_seconds": 25.4
}
```

Em caso de erro:

```json
{
  "storage_name": "Storage Name",
  "datastore_name": "Datastore Name",
  "status": "ERROR",
  "error": "Descrição do erro"
}
```

## Continue on error

Sem:

```bash
--continue-on-error
```

o batch para no primeiro erro.

Com:

```bash
--continue-on-error
```

o erro é registrado e o próximo item é processado.

Para batches grandes é recomendado:

```bash
--continue-on-error
--report resultado.json
```

## Atenção ao reexecutar batches

O script verifica se os nomes já existem.

Portanto, caso uma execução seja interrompida após alguns volumes terem sido criados, uma nova execução poderá retornar:

```text
Volume PowerStore já existe
```

ou:

```text
Datastore vCenter já existe
```

Não remova objetos automaticamente sem verificar até qual etapa o provisionamento anterior chegou.

Um volume pode ter sido:

```text
criado no PowerStore
```

mas ainda não:

```text
formatado como VMFS
```

ou o datastore pode ter sido criado mas ainda não movido para a Storage Folder.

O relatório e os logs devem ser utilizados para identificar esse estado.

## Execução recomendada para batch

```bash
./sto2vcenter.py \
  --batch volumes.json \
  --volume-group "My Volume Group" \
  --host-group "My Host Group" \
  --storage-folder "MyFolder" \
  --powerstore "https://powerstore.contoso.com" \
  --powerstore-user "user" \
  --vcenter "vcenter.contoso.com" \
  --vcenter-user "user@contoso.com" \
  --continue-on-error \
  --report resultado.json
```

## Redirecionamento para log

Além da saída do terminal, é recomendável preservar um log completo.

Exemplo:

```bash
./sto2vcenter.py \
  --batch volumes.json \
  --volume-group "My Volume Group" \
  --host-group "My Host Group" \
  --storage-folder "MyFolder" \
  --powerstore "https://powerstore.contoso.com" \
  --powerstore-user "user" \
  --vcenter "vcenter.contoso.com" \
  --vcenter-user "user@contoso.com" \
  --continue-on-error \
  --report resultado.json \
  2>&1 | tee sto2vcenter-$(date +%Y%m%d-%H%M%S).log
```

Assim ficam disponíveis:

```text
resultado.json
```

com o resultado estruturado, e:

```text
sto2vcenter-AAAAMMDD-HHMMSS.log
```

com toda a execução detalhada.

## Estrutura recomendada

```text
/opt/sto2vcenter/
├── sto2vcenter.py
├── README.md
├── batches/
│   └── volumes-batch.json
├── reports/
│   └── resultado.json
└── logs/
    └── sto2vcenter.log
```

## Resumo

O `sto2vcenter.py` automatiza de ponta a ponta:

```text
Dell PowerStore
     +
Volume Groups
     +
Host Groups
     +
NVMe
     +
VMware ESXi
     +
VMFS6
     +
vCenter Storage Folders
```

permitindo provisionar um ou vários datastores com correlação determinística entre o volume criado no storage e o device utilizado pelo VMware.
