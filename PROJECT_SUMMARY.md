# CSE-377 Game Programming: State-Aware AI NPC with MCP and a Local LLM — Proje Özeti

Bu doküman, **Unity 6 (C#)**, **Python FastAPI**, **Model Context Protocol (MCP / FastMCP)** ve **Ollama (`llama3.1:8b`)** kullanılarak geliştirilen durum farkındalığına sahip (**state-aware**) taktiksel koruma NPC'si (**Guard NPC**) projesinin mimarisini, bileşenlerini, denetim (**audit**) ve test sonuçlarını, yapılan düzeltmeleri ve çalıştırma adımlarını özetlemektedir.

---

## 1. Projenin Amacı ve Genel Mimari

Projenin temel amacı, bir oyun motorunun (**Unity 6**) oyun içi fizik, hareket, savaş kuralları, **state** yönetimi, **legal actions** filtrelemesi, **validation** ve **executor** sorumluluklarını elinde tutarken; yüksek seviyeli taktiksel karar verme sürecini **MCP tool**'ları üzerinden okuma yapan yerel bir **LLM**'e (**Ollama `llama3.1:8b`**) devretmesidir.

Temel mimari prensipleri:
- **Unity** oyun dünyasının tek gerçeklik kaynağıdır (**single source of truth**). Fizik, **per-frame** hareket, mermi/can takibi, **legal actions** üretimi, **stateVersion** kontrolü, **validation** ve **fallback** tamamen Unity tarafında deterministik olarak yürütülür.
- **MCP Server (`npc_server.py`)**, tüm sahneyi göndermek yerine yalnızca küçük ve ilgili **state** projeksiyonlarını salt-okunur (**read-only**) **MCP tool**'ları olarak sunar.
- **Local LLM (`llama3.1:8b`)**, asla doğrudan oyun kodu çalıştırmaz veya kare bazlı (**per-frame**) hareket üretmez; yalnızca o anki **legal actions** kümesi (**JSON Schema `enum`**) içinden tek bir eylem seçer.

### ASCII Veri Akış Diyagramı (Data-Flow Diagram)

```text
+-----------------------------------------------------------------------------------+
|                                  UNITY 6 CLIENT                                   |
|                                                                                   |
|  [1. Game State] ---> [2. Legal Action Generator]                                 |
|   (NpcState +          (ammo==0 -> no ATTACK,                                     |
|    stateVersion)        cover==false -> no TAKE_COVER,                            |
|                         HOLD_POSITION her zaman mevcut)                           |
|                                     |                                             |
|                                     | HTTP POST /decide (UnityWebRequest, ~3s)    |
|                                     v                                             |
+-----------------------------------------------------------------------------------+
                                      |
                                      v
+-----------------------------------------------------------------------------------+
|                            PYTHON FASTAPI AGENT (agent.py)                        |
|                                                                                   |
|  Writes state.json ---> Spawns MCP Server over stdio (npc_server.py)              |
|                                     |                                             |
|                                     v                                             |
|  [3. MCP Tools] <===> [4. Ollama Local LLM (llama3.1:8b, temperature=0)]          |
|   - get_npc_state()          - Stage 1: Bounded tool calling loop (max 4 iter)    |
|   - get_legal_actions()      - Stage 2: JSON Schema format (action enum = legal)  |
|   - get_environment_context()                                                     |
|                                     |                                             |
|                                     | Returns JSON: {action, confidence, reason,  |
|                                     |  stateVersion, toolsCalled, latencyMs}      |
+-----------------------------------------------------------------------------------+
                                      |
                                      v
+-----------------------------------------------------------------------------------+
|                                  UNITY 6 CLIENT                                   |
|                                                                                   |
|  [5. Validation & Stale Check] ---> [6. Action Executor & Debug HUD]              |
|   - response.stateVersion == currentStateVersion?                                 |
|   - action in currentLegalActions?                                                |
|   - Timeout / Stale / Invalid -> Deterministic Legal Fallback                     |
+-----------------------------------------------------------------------------------+
```

---

## 2. Dosya/Klasör Yapısı ve Önemli Script'lerin Görevleri

```text
CSE---377-Game-Programming-State-Aware-AI-NPC-with-MCP-and-a-Local-LLM/
├── README.md                                  # İngilizce kurulum ve çalıştırma kılavuzu
├── PROJECT_SUMMARY.md                         # Türkçe kapsamlı proje özeti ve rapor
├── PythonAgent/
│   ├── agent.py                               # FastAPI sunucusu (/decide, /warmup), MCP client ve Ollama LLM döngüsü
│   ├── npc_server.py                          # FastMCP tabanlı stdio MCP sunucusu (read-only state tools)
│   ├── state.json                             # Unity'den gelen son state projeksiyonunun JSON kaydı
│   └── requirements.txt                       # Python bağımlılıkları (mcp, ollama, fastapi, uvicorn, pydantic)
└── UnityClient/
    ├── Assets/
    │   ├── Scenes/
    │   │   └── SampleScene.unity              # Taktiksel arena sahnesi (Ground, GuardNPC, Player, Cover, Walls, Camera)
    │   └── Scripts/
    │       └── GuardNPCController.cs          # State, Legal Actions, Coroutine HTTP, Validation, Executor, Camera, HUD
    ├── Packages/
    │   └── manifest.json                      # Unity 6 paket tanımları (URP, InputSystem, AI Navigation vb.)
    └── ProjectSettings/                       # Unity 6 (6000.6.3f1) proje ayarları
```

- **`UnityClient/Assets/Scripts/GuardNPCController.cs`**: NPC'nin `NpcState` ve `EnvironmentContext` verilerini tutar, `GenerateLegalActions()` ile geçerli eylemleri filtreler, olay güdümlü (**event-driven**) ve zamanlayıcı (**timer**) tabanlı olarak `POST /decide` isteği gönderir, dönen yanıtı `stateVersion` ve güncel **legal actions** listesine göre doğrular (**validation**), geçersiz/zaman aşımı durumlarında **fallback** uygular, `ExecuteAction()` ile hareket/savaş eylemlerini yürütür, sabit **tactical camera** açılarını ayarlar ve ekran üstü **HUD** ile **world-space** etiketleri çizer.
- **`PythonAgent/npc_server.py`**: Resmi Python `mcp` SDK'sı (`FastMCP` / `MCPServer`) kullanarak `state.json` dosyasını okuyan üç küçük, odaklı **MCP tool** sunar: `get_npc_state()`, `get_legal_actions()` ve `get_environment_context()`.
- **`PythonAgent/agent.py`**: `FastAPI` üzerinde `POST /decide` ve `GET /warmup` uç noktalarını barındırır. Gelen isteği `asyncio.Lock()` altında `state.json`'a yazar, `stdio_client` ile `npc_server.py` alt sürecini başlatır, **Ollama** `AsyncClient` ile önce **bounded tool-calling loop** (maksimum 4 iterasyon), ardından dinamik `enum` içeren **JSON Schema** (`format=schema`, `temperature=0`) ile nihai kararı üretir.

---

## 3. Bileşenlerin Çalışma Şekli

### 3.1 Unity Tarafı (`state`, `legal actions`, `validation`, `executor`)
1. **State Temsili (`NpcState` & `EnvironmentContext`)**: Sahnenin tamamı yerine yalnızca taktiksel karar için gerekli özet veriler (`npcId`, `health`, `ammo`, `enemyDistance`, `enemyCount`, `nearbyAllies`, `coverAvailable`, `currentAction`, `stateVersion` ve `nearest_cover_distance`, `objective_under_threat`) serileştirilir.
2. **Legal Action Generator (`GenerateLegalActions`)**: Sabit 6 eylem sözlüğünden (`ATTACK`, `TAKE_COVER`, `RETREAT`, `CALL_FOR_HELP`, `RELOAD`, `HOLD_POSITION`) oyun kurallarına göre filtreleme yapar:
   - `ammo > 0 && enemyCount > 0` ise `ATTACK` eklenir (`ammo == 0` olduğunda `ATTACK` kesinlikle üretilmez).
   - `ammo < maxAmmo` ise `RELOAD` eklenir.
   - `coverAvailable == true` ise `TAKE_COVER` eklenir (`coverAvailable == false` olduğunda `TAKE_COVER` kesinlikle üretilmez).
   - `enemyCount > 0` ise `RETREAT` ve `CALL_FOR_HELP` eklenir.
   - `HOLD_POSITION` her koşulda eklenir; böylece **legal actions** listesi hiçbir zaman boş kalmaz.
3. **Event-Driven & Timer Karar Tetikleme**: Karar istekleri `Update()` içinde her karede **çağrılmaz**. 4.0 saniyelik periyodik zamanlayıcıya ek olarak; `CheckStateChangeEvents()` metodu siper durumu değiştiğinde (**cover changed**), can değiştiğinde (**damage taken / health change**), düşman sayısı değiştiğinde (**enemy seen**), mermi bittiğinde (**ammo empty**), mevcut hareket tamamlandığında (**current action finished**), müttefik sayısı değiştiğinde (**ally arrives**) veya hedef tehdidi değiştiğinde (**objective changes**) `stateVersion` değerini artırarak anında yeni karar talep eder.
4. **Validation & Stale-State Koruması**: Yanıt geldiğinde `response.stateVersion != currentStateVersion` kontrolü yapılır ve güncel `GenerateLegalActions()` yeniden hesaplanır. Yanıt **stale** ise veya seçilen eylem güncel **legal actions** içinde değilse reddedilir ve güvenli **fallback** uygulanır.
5. **Action Executor (`ExecuteAction`)**: Doğrulanan eylem `switch (action)` bloğu ile standart Unity fonksiyonlarına (`Attack()`, `TakeCover()`, `Retreat()`, `CallForHelp()`, `Reload()`, `HoldPosition()`) yönlendirilir. Kare bazlı (**per-frame**) fiziksel hareket `ExecuteContinuousMovement()` içinde `Vector3.MoveTowards` ile yürütülür.

### 3.2 MCP Server (`npc_server.py`)
- `get_npc_state()`: NPC'nin can, mermi, düşman mesafesi/sayısı, müttefik sayısı, siper durumu ve mevcut eylemini döndürür.
- `get_legal_actions()`: O an oyun kurallarına göre izin verilen **legal actions** listesini döndürür.
- `get_environment_context()`: En yakın siper mesafesini (`nearest_cover_distance`), hedef tehdit durumunu (`objective_under_threat`), siper mevcudiyetini (`cover_available`) ve yakındaki müttefik sayısını (`nearby_allies`) döndürür.
- Her şeyi tek seferde döndüren `get_everything()` gibi monolitik bir araç **yoktur**.

### 3.3 Agent (`tool loop` + `schema-constrained output`) ve FastAPI (`agent.py`)
1. **FastAPI `POST /decide`**: İsteği alır, `legalActions` boşsa LLM'i çağırmadan anında deterministik `HOLD_POSITION` **fallback** yanıtı döner. Normal durumda `state.json` dosyasını yazar ve `run_decision_loop()` fonksiyonunu çalıştırır.
2. **Aşama 1 – Bounded Tool Loop**: Maksimum 4 iterasyon boyunca `temperature=0` ile LLM'e MCP araçları (`tools`) sunulur. Model `get_npc_state`, `get_legal_actions` ve `get_environment_context` araçlarını çağırır. Eğer model herhangi bir aracı atlarsa (**skipping tool calls**), güvenlik katmanı eksik MCP aracını otomatik olarak çağırır.
3. **Aşama 2 – Schema-Constrained Final Decision**: MCP'den alınan `legal` listesi JSON Schema'nın `"action": {"type": "string", "enum": legal}` alanına yerleştirilir. `format=schema` ve `temperature=0` kullanılarak modelin yalnızca geçerli **enum** değerlerinden tek bir eylem, `0..1` arası `confidence` ve kısa bir `reason` döndürmesi garanti altına alınır.

---

## 4. Gereksinimlerin Karşılanma Tablosu (22 Audit Maddesi)

| # | Gereksinim (Requirement) | İlk Durum (Phase 1) | Son Durum (Phase 3 Sonrası) | Kodda Nerede (Dosya ve Satır Referansları) |
|---|---|---|---|---|
| **1** | Sahne: Player (Capsule), Guard NPC (Capsule), Ground (Plane), Walls/Obstacles (Cubes), Cover (Cubes); görsel ayrım; harici 3D asset yok | PARTIAL | **PASS** | `UnityClient/Assets/Scenes/SampleScene.unity` (`#L154-L265` Ground, `#L404-L514` CoverCube, `#L692-L839` GuardNPC, `#L840-L953` Player, `#L954-L1405` BackWall, LeftWall, RightWall, CoverCubeEast); `GuardNPCController.cs` (`#L161-L208` `ApplySceneVisualContrast()`) |
| **2** | `NpcState` alanları (`npcId`, `health`, `ammo`, `enemyDistance`, `enemyCount`, `nearbyAllies`, `coverAvailable`, `currentAction`, `stateVersion`) ve küçük projeksiyon | PARTIAL | **PASS** | `GuardNPCController.cs` (`#L9-L37` `NpcState` ve `DecisionRequestPayload`); `PythonAgent/agent.py` (`#L53-L74`) |
| **3** | Sabit 6 eylemli **action vocabulary** (`ATTACK`, `TAKE_COVER`, `RETREAT`, `CALL_FOR_HELP`, `RELOAD`, `HOLD_POSITION`) | PASS | **PASS** | `GuardNPCController.cs` (`#L266-L299`, `#L488-L513`); `PythonAgent/agent.py` (`#L22-L29`) |
| **4** | **Legal Action Generator** doğruluğu (`ammo == 0` -> `ATTACK` yok; `coverAvailable == false` -> `TAKE_COVER` yok; liste asla boş değil) | PASS | **PASS** | `GuardNPCController.cs` (`#L266-L299` `GenerateLegalActions()`) |
| **5** | Taktiksel seçimin **hardcoded** `if/else` olmaması | PASS | **PASS** | `PythonAgent/agent.py` (`#L123-L224` `run_decision_loop()`); `GuardNPCController.cs` (`#L336-L485`) |
| **6** | Kararların her karede `Update()` içinde çağrılmaması; **event-driven** (özellikle **cover changed** dahil) + 2–5 sn **timer** | PARTIAL | **PASS** | `GuardNPCController.cs` (`#L126-L145` `Update()`, `#L218-L257` `CheckStateChangeEvents()`, `#L566-L590` hareket bitişi tetikleyicisi) |
| **7** | `UnityWebRequest` coroutine/async yapısı, ~3 sn **timeout**, oyun donmadan güvenli davranışın sürmesi | PARTIAL | **PASS** | `GuardNPCController.cs` (`#L55-L56` `requestTimeoutSeconds = 3`, `#L336-L485` `SendDecisionRequestCoroutine()`); `SampleScene.unity` (`#L821`) |
| **8** | **Timeout**/hata durumunda deterministik ve o anki **state** için **legal** olan **fallback** (`TAKE_COVER`, `RETREAT`, `HOLD_POSITION`) | PASS | **PASS** | `GuardNPCController.cs` (`#L301-L311` `GetLegalFallbackAction()`, `#L365-L387`); `PythonAgent/agent.py` (`#L227-L235` `fallback_action()`) |
| **9** | **Stale-decision** koruması (`stateVersion` artışı, yanıtla karşılaştırma, **stale** kararın reddedilmesi ve yeni karar kuyruklama) | PARTIAL | **PASS** | `GuardNPCController.cs` (`#L259-L264` `IncrementStateVersion()`, `#L313-L321` `pendingDecisionRequest`, `#L416-L453` `STALE` kontrolü) |
| **10** | Yürütme öncesi **validation**: eylemin güncel **legal actions** içinde olduğunun kontrolü | PASS | **PASS** | `GuardNPCController.cs` (`#L414-L470`) |
| **11** | **Action Executor**: `switch` ile standart Unity hareket/savaş fonksiyonlarını çağırma, **per-frame** hareketin Unity'de olması | PASS | **PASS** | `GuardNPCController.cs` (`#L488-L590` `ExecuteAction()` ve `ExecuteContinuousMovement()`) |
| **12** | Karar başına detaylı İngilizce **logging** (time, NPC ID, state, legal actions, MCP tools, action, confidence, reason, latency, validation, executed action) | PARTIAL | **PASS** | `GuardNPCController.cs` (`#L329-L333`, `#L638-L679` `LogDecision()`) |
| **13** | Senaryo A–E'yi zorlamak için test/debug mekanizması (klavye kısayolları `1`–`5`, `6`/`B`, `C`, `F1`) | PASS | **PASS** | `GuardNPCController.cs` (`#L597-L636` `HandleScenarioHotkeys()`, `ApplyScenario()`) |
| **14** | **Camera**: tüm taktiksel alanı engelsiz (**no occlusion**) gören yükseltilmiş sabit kamera (55° pitch, 50° FOV) ve Inspector alanları | FAIL | **PASS** | `SampleScene.unity` (`#L324-L359` `Main Camera` `pos=(0, 14, -9.5)`, `rot=(55, 0, 0)`, `FOV=50`); `GuardNPCController.cs` (`#L69-L74`, `#L147-L159` `ApplyCameraConfiguration()`) |
| **15** | Ekran üstü **HUD** ve **world-space** etiketler (İngilizce, 1080p okunabilir, `F1` ile aç/kapat, oyun alanını kapatmayan yerleşim) | FAIL | **PASS** | `GuardNPCController.cs` (`#L76-L78`, `#L681-L760` `OnGUI()`, `DrawWorldLabel()`) |
| **16** | `npc_server.py`: `FastMCP` ile küçük **read-only** araçlar (`get_npc_state`, `get_legal_actions`, `get_environment_context`), iyi docstring'ler, `get_everything()` yok | PASS | **PASS** | `PythonAgent/npc_server.py` (`#L1-L57`) |
| **17** | **Agent flow**: **bounded tool loop** (max 4 iterasyon) -> dinamik `enum` içeren JSON **schema** `format`, `temperature=0`, tek eylem | PASS | **PASS** | `PythonAgent/agent.py` (`#L123-L224` `run_decision_loop()`) |
| **18** | Çağrılan **MCP tool** isimlerinin ve **latency** değerinin yakalanıp döndürülmesi | PASS | **PASS** | `PythonAgent/agent.py` (`#L128`, `#L160-L161`, `#L239`, `#L275-L283`) |
| **19** | FastAPI `POST /decide` uç noktasının `state.json` yazması, kararı çalıştırması ve `stateVersion` ile döndürmesi | PASS | **PASS** | `PythonAgent/agent.py` (`#L238-L283` `decide_endpoint()`) |
| **20** | Hata yönetimi: Ollama kapalı, bozuk JSON, modelin **tool calling** atlaması, boş **legal actions**, MCP çökmesi -> temiz İngilizce **fallback** | PARTIAL | **PASS** | `PythonAgent/agent.py` (`#L77-L113`, `#L176-L184` tool skip safeguard, `#L242-L252` boş `legalActions` kontrolü, `#L265-L273` `unwrap_exception()`, `#L286-L295` `/warmup` hata yönetimi) |
| **21** | Soğuk başlatmayı önleyen **cold-start warm-up** isteği (`GET /warmup` + `WarmupModel()`) | PASS | **PASS** | `PythonAgent/agent.py` (`#L286-L295`); `GuardNPCController.cs` (`#L323-L334`) |
| **22** | Kurulum gereksinimleri (`requirements.txt`) ve çalıştırma talimatları (`ollama pull`, `pip install`, `uvicorn`) | FAIL | **PASS** | `PythonAgent/requirements.txt` (`#L1-L5`); `README.md` (`#L1-L51`) |

---

## 5. Senaryo A–E Test Sonuçları Tablosu

Testler terminal üzerinden `PythonAgent/state.json` dosyası senaryo değerleriyle yazılarak ve `agent.py` -> `npc_server.py` (MCP stdio) -> Ollama (`llama3.1:8b`, `temperature=0`) hattı gerçek zamanlı çalıştırılarak gerçekleştirilmiştir.

> **Senaryo B Tutarsızlığı Notu (Brief vs. Infographic Discrepancy):** Yazılı proje föyünde (**written brief**) Senaryo B (Critical Health) için `ammo = 7` belirtilirken, ders infografiğinde (`infographic`) `ammo = 8` olarak yer almaktadır. Her iki varyant (`B1: ammo=7` ve `B2: ammo=8`) ayrı ayrı test edilmiş ve Unity tarafında `2` tuşuna `ammo=7`, `6` / `B` tuşuna ise `ammo=8` varyantı atanmıştır. Ayrıca yalnızca yazılı föyde bulunan **Senaryo E (Supported Defense)** de test edilmiştir.

### 5.1 Düzeltme Öncesi (Phase 2 Pre-Fix) ve Düzeltme Sonrası (Phase 3 Post-Fix) Karşılaştırmalı Tablo

| Senaryo | Durum Parametreleri (`hp, ammo, enemies, dist, allies, cover`) | Legal Actions | Çağrılan MCP Tools | Phase 2 (Önce) Seçilen Eylem | Phase 3 (Sonra) Seçilen Eylem | Confidence (Sonra) | Reason (Phase 3 Sonrası LLM Gerekçesi) | Latency (ms) |
|---|---|---|---|---|---|---|---|---|
| **A – Strong Position** | `90, 10, 1, 5m, 1, true` | `ATTACK, TAKE_COVER, RETREAT, CALL_FOR_HELP, HOLD_POSITION` | `get_npc_state, get_legal_actions, get_environment_context` | `TAKE_COVER` | **`ATTACK`** | `0.90` | *"High health and ammo, low enemy count, and cover available."* | `9,071 ms` |
| **B1 – Critical Health (Brief: ammo=7)** | `15, 7, 2, 4m, 0, true` | `ATTACK, RELOAD, TAKE_COVER, RETREAT, CALL_FOR_HELP, HOLD_POSITION` | `get_npc_state, get_legal_actions, get_environment_context` | `TAKE_COVER` | **`TAKE_COVER`** | `0.90` | *"Cover is available and enemy count is high, prioritizing cover is the best option."* | `17,795 ms` |
| **B2 – Critical Health (Infographic: ammo=8)** | `15, 8, 2, 4m, 0, true` | `ATTACK, RELOAD, TAKE_COVER, RETREAT, CALL_FOR_HELP, HOLD_POSITION` | `get_npc_state, get_legal_actions, get_environment_context` | `TAKE_COVER` | **`TAKE_COVER`** | `0.90` | *"Cover is available and enemy count is high, prioritizing cover is the best option."* | `7,693 ms` |
| **C – No Ammo** | `70, 0, 1, 7m, 2, true` | `RELOAD, TAKE_COVER, RETREAT, CALL_FOR_HELP, HOLD_POSITION` *(no `ATTACK`)* | `get_npc_state, get_legal_actions, get_environment_context` | `TAKE_COVER` | **`RELOAD`** | `1.00` | *"Ammo is 0, so I must reload to fight again."* | `19,783 ms` |
| **D – Outnumbered** | `40, 6, 4, 8m, 0, false` | `ATTACK, RELOAD, RETREAT, CALL_FOR_HELP, HOLD_POSITION` *(no `TAKE_COVER`)* | `get_npc_state, get_legal_actions, get_environment_context` | `RETREAT` | **`RETREAT`** | `1.00` | *"Outnumbered with no cover available and low health."* | `10,695 ms` |
| **E – Supported Defense** | `55, 4, 2, 12m, 3, true` | `ATTACK, RELOAD, TAKE_COVER, RETREAT, CALL_FOR_HELP, HOLD_POSITION` | `get_npc_state, get_legal_actions, get_environment_context` | `TAKE_COVER` | **`ATTACK`** | `0.90` | *"High health, plentiful ammo, and low enemy count allow for a strong attack."* | `25,019 ms` |

### 5.2 İnfografikteki Beklenen Öğrenme Çıktısı (Expected A/B/C Pattern) ile Karşılaştırma
- **İnfografik Beklentisi**:
  - **State A** (yüksek can, tam mermi, 1 düşman, siper var) -> `ATTACK`
  - **State B** (kritik can, birden fazla düşman) -> `RETREAT` veya `TAKE_COVER`
  - **State C** (mermi yok, düşman yakında) -> `RELOAD`
- **Değerlendirme**:
  - **Phase 2 (Düzeltme Öncesi)**: `agent.py` içindeki ikinci `llm.chat(..., format=schema)` çağrısında `tools` parametresi verilmediği halde geçmiş mesajlarda ham `tool_calls` ve `role: "tool"` mesajları bırakıldığı için Ollama'nın `llama3.1` şablonu araç çıktılarını ikinci aşamada sağlıklı işleyememiş ve `TAKE_COVER` yasal olduğu her senaryoda (`A, B1, B2, C, E`) aynı genel gerekçeyle (`"To protect myself from potential enemy fire"`) `TAKE_COVER` seçmiştir.
  - **Phase 3 (Düzeltme Sonrası)**: MCP araçlarından toplanan gerçek çıktılar Stage 2 istemine temiz bir formatta aktarıldığında ve `SYSTEM_PROMPT` taktiksel bağlamla güçlendirildiğinde, `llama3.1:8b` modeli **hiçbir hardcoded kural olmadan**:
    - **State A** için **`ATTACK`** (`conf=0.90`),
    - **State B1 (`ammo=7`)** ve **State B2 (`ammo=8`)** için **`TAKE_COVER`** (`conf=0.90`),
    - **State C (`ammo=0`)** için **`RELOAD`** (`conf=1.00`),
    - **State D (`cover=false, enemies=4`)** için **`RETREAT`** (`conf=1.00`),
    - **State E (`allies=3, cover=true`)** için **`ATTACK`** (`conf=0.90`)
    seçerek infografikteki **A -> ATTACK, B -> TAKE_COVER, C -> RELOAD** örüntüsüyle **%100 uyum** sağlamıştır.

---

## 6. Latency/Timeout, Stale State ve Fallback Mekanizmalarının Açıklaması

1. **Latency ve Timeout Yönetimi**:
   - Unity tarafında tüm HTTP istekleri `SendDecisionRequestCoroutine()` içinde `UnityWebRequest` ile asenkron olarak yürütülür; ana oyun döngüsü (`Update()`) ve `ExecuteContinuousMovement()` asla bloklanmaz.
   - `requestTimeoutSeconds` varsayılan olarak **3 saniye** (`3s`) olarak ayarlanmıştır (Inspector üzerinden donanım hızına göre artırılabilir).
   - Oyun başlangıcında `Start()` içinde `WarmupModel()` coroutine'i `GET /warmup` uç noktasını çağırarak `llama3.1:8b` modelini RAM/VRAM'e önceden yükler (`keep_alive="30m"`).
2. **Stale State (`stateVersion`) Koruması**:
   - Unity'deki `currentStateVersion` sayacı her senaryo değişiminde ve her **event-driven** durum değişiminde (`IncrementStateVersion`) artırılır ve `DecisionRequestPayload.stateVersion` (ve `NpcState.stateVersion`) olarak Python sunucusuna gönderilir.
   - Eğer bir karar isteği beklenirken (`isWaitingForDecision == true`) yeni bir durum değişikliği olursa, `pendingDecisionRequest = true` bayrağı kaldırılır.
   - Python'dan yanıt döndüğünde `bool isStale = (response.stateVersion != currentStateVersion)` kontrol edilir. Eğer yanıt eski bir duruma aitse (`isStale == true`), `Validation Result` **`STALE`** (veya hem eski hem yasal değilse **`STALE & REJECTED`**) olarak işaretlenir, eski karar reddedilerek o anki duruma uygun güvenli **fallback** uygulanır ve bekleyen güncel `stateVersion` için anında yeni bir karar isteği başlatılır.
3. **Deterministik ve Legal Fallback Mekanizması**:
   - Hem Unity (`GetLegalFallbackAction`) hem de Python (`fallback_action`) tarafında hata, **timeout**, bozuk JSON, boş **legal actions** veya **Ollama** servisinin kapalı olması durumunda oyun asla çökmez.
   - **Fallback** seçimi deterministik olarak güncel `legalActions` listesi içinden sırasıyla `TAKE_COVER` -> `RETREAT` -> `HOLD_POSITION` önceliğiyle yapılır. Örneğin `coverAvailable == false` olduğunda `TAKE_COVER` `legalActions` içinde yer almadığından asla **fallback** olarak seçilmez; bunun yerine yasal olan `RETREAT` veya `HOLD_POSITION` çalıştırılır.

---

## 7. Logging Örneği (Gerçek Test Çıktısı)

Aşağıda `GuardNPCController.LogDecision()` formatında kaydedilen gerçek İngilizce karar günlüğü (**decision log**) örneği yer almaktadır:

```text
[AI DECISION LOG - 19:20:09]
NPC: guard_01 (RequestStateVersion: 4, CurrentStateVersion: 4)
State: Health=70, Ammo=0, Enemies=1, Dist=7m, Allies=2, Cover=True, CurrentAction=HOLD_POSITION
Legal Actions: RELOAD, TAKE_COVER, RETREAT, CALL_FOR_HELP, HOLD_POSITION
MCP Tools Called: get_npc_state, get_legal_actions, get_environment_context
Selected Action: RELOAD
Confidence: 1.00
Reason: Ammo is 0, so I must reload to fight again.
Latency: 19783 ms
Validation Result: PASS
Executed Action: RELOAD
```

---

## 8. Kamera ve HUD Yapılandırması

### 8.1 Final Camera Setup ve Gerekçesi
- **Position**: `(0.0, 14.0, -9.5)`
- **Rotation (Euler Angles)**: `(55.0°, 0.0°, 0.0°)` *(Quaternion: `x=0.46174863, y=0, z=0, w=0.8870108`)*
- **Projection & Field of View (FOV)**: Perspective, `FOV = 50°`
- **Gerekçe ve Frustum/Occlusion Doğrulaması**:
  - Orijinal sahnede kamera `(0, 1, -10)` konumunda ve `(0°, 0°, 0°)` açısında (yerden 1 metre yüksekte, yatay) durduğu için `(0, 1, 0)` noktasındaki `GuardNPC` ile `(0, 1, 5)` noktasındaki `Player (Enemy)` ekranın tam ortasında (`viewport = (0.500, 0.500)`) üst üste biniyor (**occlusion**) ve birbirini kapatıyordu.
  - Yeni **55° pitch** açılı yükseltilmiş (**elevated top-down / isometric**) kamera kurulumunda tüm nesneler **16:9**, **16:10** ve **4:3** ekran oranlarında güvenli görüş alanı (`0.05 <= vx, vy <= 0.95`) içinde kalmaktadır:
    - `GuardNPC (0, 1, 0)` -> Viewport `(0.500, 0.522)`
    - `Player (0, 1, 5)` -> Viewport `(0.500, 0.750)`
    - `CoverCube (-3, 0.5, 2)` -> Viewport `(0.398, 0.602)`
    - `CoverCubeEast (3.5, 0.5, 1.5)` -> Viewport `(0.622, 0.578)`
    - `RetreatPoint (0, 1, -4)` -> Viewport `(0.500, 0.271)`
  - Kameradan `GuardNPC` ve `Player` nesnelerine yapılan **linecast / ray-box intersection** kontrollerinde hiçbir duvarın veya siperin görüşü engellemediği (`occluded_by = []`) doğrulanmıştır.
  - Kamera parametreleri `GuardNPCController` üzerinde `cameraPosition`, `cameraEulerAngles` ve `cameraFieldOfView` olarak serileştirilmiştir (**Inspector** üzerinden düzenlenebilir).

### 8.2 Ekran Üstü HUD (On-Screen Debug HUD)
- `GuardNPCController.OnGUI()` içinde sol üst köşede (`x=14, y=14`), 1080p çözünürlüğe göre dinamik ölçeklenen yüksek kontrastlı yarı saydam bir bilgi paneli çizilir (sol %22'lik alanda yer aldığı için orta alandaki hiçbir oyun nesnesini kapatmaz).
- **HUD üzerinde gösterilen İngilizce bilgiler**:
  1. **AI Service Status**: `online` / `offline` / `timeout` / `request in flight...`
  2. **Active Scenario & NPC State**: `npcId`, `stateVersion`, `health`, `ammo/maxAmmo`, `enemyCount`, `enemyDistance`, `nearbyAllies`, `coverAvailable`, `nearest_cover_distance`, `objective_under_threat`, `currentAction`
  3. **Current Legal Actions**: O anki geçerli eylem listesi
  4. **Last AI Decision**: Seçilen `action`, `confidence`, `latencyMs`, çağrılan **MCP tools** ve modelin `reason` açıklaması
  5. **Validation Result & Executed Action**: `PASS` / `REJECTED` / `STALE` / `STALE & REJECTED` / `FALLBACK` durumu ve fiilen yürütülen eylem
  6. **World-Space Labels**: Sahnedeki `Guard`, `Player (Enemy)`, `Cover` ve `Retreat Point` nesnelerinin üzerinde 3B dünyadan 2B ekrana izdüşürülmüş İngilizce etiketler (`F1` tuşu ile **HUD** ve etiketler açılıp kapatılabilir).

---

## 9. Bu Oturumda Bulunan ve Düzeltilen Hatalar (CHANGELOG)

1. **`PythonAgent/npc_server.py`**:
   - `load_state()` fonksiyonuna eksik veya bozuk `state.json` dosyaları için doğrulama ve anlamlı hata fırlatma eklendi.
   - `get_environment_context()` aracı, `nearest_cover_distance` ve `objective_under_threat` alanlarına ek olarak `cover_available` ve `nearby_allies` bilgisini de döndürecek şekilde zenginleştirildi.
2. **`PythonAgent/agent.py`**:
   - **Kritik Hata Düzeltmesi (`parse_legal_actions` & `tool_result_text`)**: `state.json` bozuk olduğunda FastMCP'nin döndürdüğü `"Error executing tool get_legal_actions"` hata metninin (`res.isError = True`) geçerli bir **legal action** sanılıp JSON Schema `enum` listesine eklenmesi hatası giderildi; `res.isError` kontrolü, `VALID_ACTIONS` doğrulaması ve `run_decision_loop()` başında hızlı `state.json` doğrulaması eklendi.
   - **Boş `legalActions` (`[]`) Koruması**: `POST /decide` uç noktasına boş `legalActions` geldiğinde LLM'e geçersiz `"enum": []` gönderip 10 saniye sonunda `HTTP 400 Bad Request` almak yerine, `0 ms` içinde deterministik `HOLD_POSITION` **fallback** döndürülmesi sağlandı.
   - **Tool Skipping Safeguard**: Modelin Aşama 1'de herhangi bir zorunlu **MCP tool**'u (`get_npc_state`, `get_legal_actions`, `get_environment_context`) çağırmayı atlaması durumunda eksik araçların otomatik olarak MCP üzerinden çağrılması ve aynı turda tekrarlanan araç çağrılarının tekilleştirilmesi (**deduplication**) sağlandı.
   - **Aşama 2 İstem ve Şablon Düzeltmesi**: Aşama 2'deki `format=schema` çağrısında toplanan **MCP tool** gözlemleri temiz bir mesaj yapısıyla modele sunularak `llama3.1:8b`'nin tüm senaryolarda sadece `TAKE_COVER` seçmesi sorunu çözüldü.
   - **Hata Yönetimi (`unwrap_exception` & `/warmup`)**: `anyio` `ExceptionGroup` sarmalayıcısını açan `unwrap_exception()` eklendi; `/warmup` uç noktasına `try/except` eklenerek Ollama kapalıyken HTTP 500 çökmesi önlendi; tüm Türkçe yorumlar İngilizceye çevrildi.
3. **`UnityClient/Assets/Scripts/GuardNPCController.cs`**:
   - `NpcState` sınıfına `stateVersion` alanı eklendi.
   - `requestTimeoutSeconds` varsayılan değeri `30` saniyeden şartnamedeki **3 saniyeye** (`3s`) çekildi ve `Start()` içindeki zorunlu `MinRequestTimeoutSeconds = 30` ezmesi kaldırıldı.
   - **Event-Driven Karar Tetikleyicileri (`CheckStateChangeEvents`)**: Siper değişimi (**cover changed**), can değişimi, düşman sayısı değişimi, mermi bitişi (`ammo == 0`), müttefik gelişi, hedef tehdidi değişimi ve hareket tamamlanması (`ExecuteContinuousMovement`) olaylarında `stateVersion` artışı ve otomatik karar isteği eklendi.
   - **Stale State Koruması**: İstek uçuş halindeyken (`isWaitingForDecision`) yeni bir durum oluştuğunda isteğin yutulması hatası `pendingDecisionRequest` kuyruğu ile düzeltildi; `response.stateVersion != currentStateVersion` olan tüm yanıtların `STALE` olarak reddedilmesi sağlandı.
   - **Kamera, Görsel Kontrast ve HUD**: 55° eğimli sabit taktiksel kamera ayarları (`ApplyCameraConfiguration`), nesne renk kontrastı (`ApplySceneVisualContrast`) ve `F1` ile açılıp kapanabilen İngilizce `OnGUI()` **HUD** + **world-space** etiketleri eklendi.
   - Tüm Türkçe `Debug.Log` mesajları ve kod içi yorumlar İngilizceye çevrildi; Senaryo B'nin `ammo=8` infografik varyantı için `6`/`B` ve siper testi için `C` kısayolları eklendi.
4. **`UnityClient/Assets/Scenes/SampleScene.unity`**:
   - `Main Camera` konumu `(0, 14, -9.5)`, rotasyonu `(55, 0, 0)` ve `FOV` değeri `50` olarak güncellendi.
   - `Enemy` nesnesinin adı `Player` olarak güncellendi (`fileID` referansları korundu).
   - `RetreatPoint` `(0.28, 0.20, 0.28)` konumundan `(0, 1, -4)` konumuna taşındı.
   - Sahneye `BackWall`, `LeftWall`, `RightWall` duvar küpleri ve ikinci siper küpü (`CoverCubeEast`) eklendi; `Directional Light` gölge sertliği `0.35`'e düşürüldü.
5. **`PythonAgent/requirements.txt` & `README.md`**:
   - Eksik olan `PythonAgent/requirements.txt` oluşturuldu ve `README.md` dosyasına tüm kurulum/çalıştırma adımları İngilizce olarak eklendi.

---

## 10. Bilinen Eksikler / Sınırlamalar ve Opsiyonel Geliştirmeler

- **Donanım Kaynaklı LLM Latency Süresi**: Yerel `llama3.1:8b` modeli ile iki aşamalı (**Stage 1 MCP tool calling + Stage 2 JSON Schema output**) karar döngüsü test ortamında ~7.7 sn ile ~19.7 sn arasında sürmektedir. Unity'deki `requestTimeoutSeconds = 3` ayarı şartname gereği ~3 saniyeye ayarlandığından, yavaş CPU/GPU donanımlarında 3 saniye içinde yanıt dönmezse Unity deterministik **fallback** (`TAKE_COVER` veya `HOLD_POSITION`) çalıştırır. Gerçek zamanlı LLM yanıtlarını Unity içinde beklemek için Inspector üzerinden `requestTimeoutSeconds` değeri `20`–`30` saniyeye çıkarılabilir.
- **Hareket Sistemi (`Vector3.MoveTowards` vs. `NavMesh`)**: Mevcut hareket yürütücüsü (`ExecuteContinuousMovement`) doğrudan `Vector3.MoveTowards` kullanmaktadır; karmaşık engellerin etrafından dolaşmak için Unity `NavMeshAgent` bileşeni opsiyonel olarak eklenebilir.
- **Unity Editor GUI Etkileşimli Play Mode Testi (`TEST EDİLMEDİ`)**: C# script'leri `dotnet build` ile Unity 6 (`6000.6.3f1`) kütüphanelerine karşı **0 hata ve 0 uyarı** ile derlenmiş, kamera **frustum/occlusion** hesaplamaları matematiksel olarak doğrulanmış ve Python **agent + MCP server** uçtan uca terminalde gerçek **Ollama** üzerinde test edilmiştir; ancak başsız (**headless**) terminal ortamında Unity Editor GUI penceresi açılarak görsel Play Mode tıklama testi **TEST EDİLMEDİ**.
- **Opsiyonel Geliştirmeler (Gelecek Çalışmalar)**:
  - **Multiple NPC Personalities**: `NpcState` içine `personality` (`"aggressive"`, `"cautious"`, `"support"`) alanı eklenerek farklı koruma NPC'lerinin aynı durumda farklı taktiksel tercihler yapması sağlanabilir.
  - **NPC Cooperation**: Birden fazla Guard NPC'nin ortak bir `squad_state` MCP aracı üzerinden birbirlerinin hedef ve eylemlerini görerek koordineli kuşatma veya nöbetleşe şarjör değiştirme (`RELOAD` + `ATTACK`) yapması sağlanabilir.

---

## 11. Projeyi Çalıştırma Adımları

### Adım 1: Ollama Kurulumu ve Modelin İndirilmesi
Ollama servisinin çalıştığından emin olun ve yerel modeli indirin:
```bash
ollama pull llama3.1:8b
```

### Adım 2: Python Paketlerinin Kurulması
Proje kök dizininde Python bağımlılıklarını yükleyin:
```bash
pip install mcp ollama fastapi uvicorn pydantic
# veya:
pip install -r PythonAgent/requirements.txt
```

### Adım 3: FastAPI + MCP Agent Sunucusunun Başlatılması
`PythonAgent` klasörüne geçerek `uvicorn` sunucusunu başlatın:
```bash
cd PythonAgent
uvicorn agent:app --host 127.0.0.1 --port 8000
```
*(Sunucu kontrolü için tarayıcıdan veya terminalden `http://127.0.0.1:8000/` ve `http://127.0.0.1:8000/warmup` adreslerini ziyaret edebilirsiniz.)*

### Adım 4: Unity 6 Projesinin Çalıştırılması
1. **Unity Hub** üzerinden `UnityClient` klasörünü **Unity 6 (`6000.x`)** ile açın.
2. Project penceresinden `Assets/Scenes/SampleScene.unity` sahnesini açın.
3. **Play** butonuna basın.
4. Oyun çalışırken klavye kısayolları ile senaryoları test edin:
   - `1`: **Scenario A – Strong Position** (`health=90, ammo=10, enemies=1, dist=5, allies=1, cover=true`)
   - `2`: **Scenario B – Critical Health (Brief)** (`health=15, ammo=7, enemies=2, dist=4, allies=0, cover=true`)
   - `6` veya `B`: **Scenario B – Critical Health (Infographic)** (`health=15, ammo=8, enemies=2, dist=4, allies=0, cover=true`)
   - `3`: **Scenario C – No Ammunition** (`health=70, ammo=0, enemies=1, dist=7, allies=2, cover=true`)
   - `4`: **Scenario D – Outnumbered** (`health=40, ammo=6, enemies=4, dist=8, allies=0, cover=false`)
   - `5`: **Scenario E – Supported Defense** (`health=55, ammo=4, enemies=2, dist=12, allies=3, cover=true`)
   - `C`: `coverAvailable` durumunu anlık değiştir (**cover changed** olay tetikleyicisini test eder)
   - `F1`: Ekran üstü **Debug HUD** ve **world-space** etiketlerini aç/kapat

