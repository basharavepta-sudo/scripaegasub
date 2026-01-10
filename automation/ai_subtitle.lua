script_name = "AI Subtitle Assistant"
script_description = "AI-powered subtitle editing with ALMA/Ollama"
script_author = "AI Assistant"
script_version = "3.0"

-- Настройка путей
local script_path = debug.getinfo(1).source:match("@?(.*[\\/])")
if not script_path then
    script_path = aegisub.decode_path("?user/automation/autoload/")
end

local separator = package.config:sub(1,1)
package.path = package.path .. ";" .. script_path .. "modules" .. separator .. "?.lua"

local json = require("json")

-- Пути к файлам
local temp_dir = os.getenv("TEMP") or os.getenv("TMP") or "/tmp"
local request_file = temp_dir .. separator .. "aegisub_ai_request.json"
local response_file = temp_dir .. separator .. "aegisub_ai_response.json"
local python_script = script_path .. "alma_backend.py"
local config_file = script_path .. "config.json"
local settings_file = script_path .. "user_settings.json"

-- Кэш настроек сессии
local session_settings = nil

-- ============== Утилиты ==============

local function get_python_cmd()
    if separator == "/" then
        local handle = io.popen("which python3 2>/dev/null")
        if handle then
            local result = handle:read("*a")
            handle:close()
            if result and result ~= "" then
                return "python3"
            end
        end
        return "python"
    else
        return "python"
    end
end

local python_executable = get_python_cmd()

local function read_json_file(filepath)
    local file = io.open(filepath, "r")
    if not file then return nil end
    local content = file:read("*all")
    file:close()
    if not content or content == "" then return nil end
    local ok, data = pcall(json.decode, content)
    return ok and data or nil
end

local function write_json_file(filepath, data)
    local file = io.open(filepath, "w")
    if not file then return false end
    local ok, encoded = pcall(json.encode, data)
    if not ok then file:close() return false end
    file:write(encoded)
    file:close()
    return true
end

-- ============== Настройки ==============

local function load_settings()
    if session_settings then return session_settings end

    -- Загружаем из файла или создаём дефолтные
    session_settings = read_json_file(settings_file) or {
        global_context = "",
        default_instructions = "",
        translation_style = "natural",
        num_variants = 3,
        context_lines = 1,
        use_source = false
    }
    return session_settings
end

local function save_settings()
    if session_settings then
        write_json_file(settings_file, session_settings)

        -- Также обновляем config.json
        local config = read_json_file(config_file) or {}
        config.global_context = session_settings.global_context
        config.default_instructions = session_settings.default_instructions
        config.translation_style = session_settings.translation_style
        config.num_variants = session_settings.num_variants
        write_json_file(config_file, config)
    end
end

-- ============== UI Настроек проекта ==============

local function load_backend_config()
    return read_json_file(config_file) or {
        backend = "ollama",
        ollama_url = "http://localhost:11434",
        model = "llama3",
        temperature = 0.7,
        max_tokens = 600,
        enable_images = false,
        image_model = "sdxl"
    }
end

local function show_project_settings()
    local settings = load_settings()
    local backend_config = load_backend_config()

    local style_items = {"natural", "formal", "casual", "literal"}
    local backend_items = {"ollama", "transformers"}
    local model_items = {"llama3", "llama3.1", "llama3.2", "mistral", "qwen2", "qwen2.5", "gemma2", "gemma3", "phi3"}
    local image_model_items = {"sdxl", "sd3", "flux", "dall-e"}

    local dialog = {
        -- Секция: Перевод
        {class="label", label="═══ НАСТРОЙКИ ПЕРЕВОДА ═══", x=0, y=0, width=4},

        {class="label", label="Глобальный контекст:", x=0, y=1, width=2},
        {class="textbox", name="global_context", value=settings.global_context or "", x=0, y=2, width=4, height=2},

        {class="label", label="Инструкции по умолчанию:", x=0, y=4, width=2},
        {class="textbox", name="default_instructions", value=settings.default_instructions or "", x=0, y=5, width=4, height=2},

        {class="label", label="Стиль:", x=0, y=7},
        {class="dropdown", name="style", items=style_items, value=settings.translation_style or "natural", x=1, y=7, width=1},

        {class="label", label="Вариантов:", x=2, y=7},
        {class="intedit", name="num_variants", value=settings.num_variants or 3, min=1, max=5, x=3, y=7},

        {class="label", label="Строк контекста:", x=0, y=8},
        {class="intedit", name="context_lines", value=settings.context_lines or 1, min=0, max=5, x=1, y=8},

        {class="checkbox", name="use_source", label="Использовать англ. исходник", value=settings.use_source or false, x=2, y=8, width=2},

        -- Секция: Backend/AI
        {class="label", label="═══ НАСТРОЙКИ AI ═══", x=0, y=9, width=4},

        {class="label", label="Backend:", x=0, y=10},
        {class="dropdown", name="backend", items=backend_items, value=backend_config.backend or "ollama", x=1, y=10},

        {class="label", label="Модель:", x=2, y=10},
        {class="dropdown", name="model", items=model_items, value=backend_config.model or "llama3", x=3, y=10},

        {class="label", label="Ollama URL:", x=0, y=11},
        {class="edit", name="ollama_url", value=backend_config.ollama_url or "http://localhost:11434", x=1, y=11, width=3},

        {class="label", label="Temperature:", x=0, y=12},
        {class="floatedit", name="temperature", value=backend_config.temperature or 0.7, min=0.0, max=2.0, step=0.1, x=1, y=12},

        {class="label", label="Max tokens:", x=2, y=12},
        {class="intedit", name="max_tokens", value=backend_config.max_tokens or 600, min=100, max=2000, x=3, y=12},

        -- Секция: Изображения
        {class="label", label="═══ ГЕНЕРАЦИЯ КАРТИНОК ═══", x=0, y=13, width=4},

        {class="checkbox", name="enable_images", label="Генерировать картинки сцен", value=backend_config.enable_images or false, x=0, y=14, width=2},

        {class="label", label="Image модель:", x=2, y=14},
        {class="dropdown", name="image_model", items=image_model_items, value=backend_config.image_model or "sdxl", x=3, y=14},
    }

    local buttons = {"Сохранить", "Отмена"}
    local button, results = aegisub.dialog.display(dialog, buttons)

    if button == "Сохранить" then
        -- Обновляем настройки сессии
        session_settings.global_context = results.global_context
        session_settings.default_instructions = results.default_instructions
        session_settings.translation_style = results.style
        session_settings.num_variants = results.num_variants
        session_settings.context_lines = results.context_lines
        session_settings.use_source = results.use_source

        -- Сохраняем настройки сессии
        save_settings()

        -- Обновляем config.json с настройками бэкенда
        local config = read_json_file(config_file) or {}
        config.backend = results.backend
        config.model = results.model
        config.ollama_url = results.ollama_url
        config.temperature = results.temperature
        config.max_tokens = results.max_tokens
        config.enable_images = results.enable_images
        config.image_model = results.image_model
        config.global_context = results.global_context
        config.default_instructions = results.default_instructions
        config.translation_style = results.style
        config.num_variants = results.num_variants
        write_json_file(config_file, config)

        aegisub.log("Все настройки сохранены!\n")
    end
end

-- ============== Парсинг SRT ==============

local function parse_srt_file(filepath)
    local file = io.open(filepath, "r")
    if not file then return nil end

    local content = file:read("*all")
    file:close()

    local blocks = {}
    local current_block = {}
    local block_num = nil

    for line in content:gmatch("[^\r\n]+") do
        if line:find("^\239\187\191") then
            line = line:sub(4)
        end
        if line:match("^%d+$") then
            if block_num and current_block.text then
                blocks[block_num] = current_block.text
            end
            block_num = tonumber(line)
            current_block = {text = ""}
        elseif line:match("^%d%d:%d%d:%d%d") then
            -- таймкод
        elseif line ~= "" and block_num then
            if current_block.text and current_block.text ~= "" then
                current_block.text = current_block.text .. "\n"
            end
            current_block.text = (current_block.text or "") .. line
        end
    end
    if block_num and current_block.text then
        blocks[block_num] = current_block.text
    end

    return blocks
end

-- ============== Запуск Python ==============

local function delete_file(path)
    os.remove(path)
end

local function get_file_timestamp()
    return os.time()
end

local function run_python_backend()
    -- ВАЖНО: удаляем старый response файл перед запуском!
    -- Это исправляет баг когда повторный перевод возвращал старые данные
    delete_file(response_file)

    local timestamp_before = get_file_timestamp()

    local cmd
    if separator == "/" then
        -- Linux/Mac: просто запускаем
        cmd = python_executable .. ' "' .. python_script .. '" "' .. request_file .. '" "' .. response_file .. '" "' .. config_file .. '" 2>&1'
    else
        -- Windows: используем pythonw для скрытия окна CMD если доступен
        -- Иначе используем start /b /wait
        local pythonw_test = io.popen("where pythonw 2>nul")
        local pythonw_path = pythonw_test and pythonw_test:read("*a") or ""
        pythonw_test:close()

        if pythonw_path and pythonw_path ~= "" then
            cmd = 'pythonw "' .. python_script .. '" "' .. request_file .. '" "' .. response_file .. '" "' .. config_file .. '"'
        else
            cmd = 'start /b /wait "" ' .. python_executable .. ' "' .. python_script .. '" "' .. request_file .. '" "' .. response_file .. '" "' .. config_file .. '"'
        end
    end

    -- Запускаем процесс
    local handle = io.popen(cmd .. " && echo __SUCCESS__ || echo __FAILED__", "r")
    if handle then
        local output = handle:read("*a")
        handle:close()
    end

    -- Проверяем что response файл создан ПОСЛЕ запуска
    local f = io.open(response_file, "r")
    if f then
        local content = f:read("*a")
        f:close()
        if content and content ~= "" then
            -- Проверяем что файл содержит валидный JSON
            local ok, data = pcall(json.decode, content)
            if ok and data then
                return true
            end
        end
    end
    return false
end

-- ============== Главная функция перевода ==============

local function translate_line(subs, sel, active)
    local settings = load_settings()
    local line = subs[active]

    if line.class ~= "dialogue" then
        aegisub.dialog.display({{class="label", label="Выберите строку диалога!"}}, {"OK"})
        return
    end

    -- Короткий контекст для отображения
    local ctx_preview = settings.global_context or ""
    if #ctx_preview > 40 then
        ctx_preview = ctx_preview:sub(1, 40) .. "..."
    end
    if ctx_preview == "" then
        ctx_preview = "(не задан - нажми Настройки)"
    end

    -- Диалог быстрых настроек
    local quick_dialog = {
        {class="label", label="Текущая строка: " .. (line.text:sub(1, 50) .. (#line.text > 50 and "..." or "")), x=0, y=0, width=3},
        {class="label", label="", x=0, y=1},

        -- Быстрое отображение/редактирование контекста
        {class="label", label="Контекст проекта:", x=0, y=2},
        {class="edit", name="global_context", value=settings.global_context or "", x=0, y=3, width=3},

        {class="label", label="Инструкции для этого перевода:", x=0, y=4, width=3},
        {class="textbox", name="instructions", value="", x=0, y=5, width=3, height=2},

        {class="label", label="Вариантов:", x=0, y=7},
        {class="intedit", name="num_variants", value=settings.num_variants, min=1, max=5, x=1, y=7},

        {class="label", label="Стиль:", x=0, y=8},
        {class="dropdown", name="style", items={"natural", "formal", "casual", "literal"}, value=settings.translation_style or "natural", x=1, y=8, width=2},
    }

    local btn, res = aegisub.dialog.display(quick_dialog, {"Перевести", "Настройки", "Отмена"})

    if btn == "Настройки" then
        show_project_settings()
        return translate_line(subs, sel, active)  -- Повторить после настройки
    elseif btn == "Отмена" then
        return
    end

    -- Сохраняем изменённый контекст и стиль
    if res.global_context ~= session_settings.global_context or res.style ~= session_settings.translation_style then
        session_settings.global_context = res.global_context
        session_settings.translation_style = res.style
        save_settings()
    end

    -- Загружаем source если нужно
    local source_blocks = nil
    if settings.use_source then
        local sub_path = aegisub.decode_path("?script")
        local sub_name = aegisub.file_name()
        if sub_name then
            sub_name = sub_name:match("(.+)%..+$") or sub_name
        else
            sub_name = "unknown"
        end

        for _, ext in ipairs({".txt", ".srt", "_en.txt", "_en.srt"}) do
            local try_path = sub_path .. separator .. sub_name .. ext
            local f = io.open(try_path, "r")
            if f then
                f:close()
                source_blocks = parse_srt_file(try_path)
                break
            end
        end
    end

    -- Строим маппинг диалогов
    local line_id_map = {}
    local dialogue_count = 0
    for i = 1, #subs do
        if subs[i].class == "dialogue" then
            dialogue_count = dialogue_count + 1
            line_id_map[i] = dialogue_count
        end
    end

    local current_d_idx = line_id_map[active]

    -- Собираем контекст
    local context_before = {}
    local context_after = {}
    local ctx_size = settings.context_lines or 1

    for i = math.max(1, active - ctx_size), active - 1 do
        if subs[i].class == "dialogue" then
            local d_idx = line_id_map[i]
            table.insert(context_before, {
                ru = subs[i].text,
                en = source_blocks and d_idx and source_blocks[d_idx] or ""
            })
        end
    end

    for i = active + 1, math.min(#subs, active + ctx_size) do
        if subs[i].class == "dialogue" then
            local d_idx = line_id_map[i]
            table.insert(context_after, {
                ru = subs[i].text,
                en = source_blocks and d_idx and source_blocks[d_idx] or ""
            })
        end
    end

    -- Формируем запрос
    local request_data = {
        current_line = {
            ru = line.text,
            en = source_blocks and source_blocks[current_d_idx] or "",
            duration = (line.end_time - line.start_time) / 1000.0
        },
        context_before = context_before,
        context_after = context_after,
        feedback = res.instructions  -- Инструкции пользователя
    }

    write_json_file(request_file, request_data)

    -- Обновляем настройки в config
    local config = read_json_file(config_file) or {}
    config.num_variants = res.num_variants
    config.global_context = res.global_context
    config.translation_style = res.style
    write_json_file(config_file, config)

    -- Запускаем
    aegisub.progress.task("AI думает...")
    local success = run_python_backend()

    if not success then
        aegisub.dialog.display({{class="label", label="Ошибка Python! Проверьте Ollama."}}, {"OK"})
        return
    end

    local response = read_json_file(response_file)
    if not response then
        aegisub.dialog.display({{class="label", label="Нет ответа от AI."}}, {"OK"})
        return
    end

    if response.error then
        aegisub.dialog.display({{class="label", label="Ошибка: " .. response.error}}, {"OK"})
        return
    end

    if not response.variants or #response.variants == 0 then
        aegisub.dialog.display({{class="label", label="AI не вернул вариантов."}}, {"OK"})
        return
    end

    -- Показываем результаты с картинкой если есть
    show_result_dialog(subs, active, line, response, request_data, source_blocks, res.num_variants)
end

-- ============== Открытие картинки ==============

local function open_image(image_path)
    if not image_path then return end

    local cmd
    if separator == "/" then
        -- Linux/Mac
        if os.execute("which xdg-open >/dev/null 2>&1") == 0 then
            cmd = 'xdg-open "' .. image_path .. '" &'
        elseif os.execute("which open >/dev/null 2>&1") == 0 then
            cmd = 'open "' .. image_path .. '" &'
        end
    else
        -- Windows
        cmd = 'start "" "' .. image_path .. '"'
    end

    if cmd then
        os.execute(cmd)
    end
end

-- ============== Диалог результатов ==============

function show_result_dialog(subs, active, line, response, request_data, source_blocks, num_variants)
    local variants = response.variants
    local image_path = response.image_path
    local scene_desc = response.scene_description

    local truncate = function(text, max)
        max = max or 60
        return #text > max and text:sub(1, max) .. "..." or text
    end

    local dialog = {
        {class="label", label="Оригинал: " .. truncate(line.text), x=0, y=0, width=4},
    }

    -- Показываем инфо о картинке если есть
    local y_offset = 1
    if image_path then
        table.insert(dialog, {class="label", label="🖼 Картинка сгенерирована: " .. truncate(scene_desc or "сцена", 40), x=0, y=y_offset, width=4})
        y_offset = y_offset + 1
    end

    table.insert(dialog, {class="label", label="", x=0, y=y_offset})
    y_offset = y_offset + 1

    -- Варианты
    local dropdown_items = {}
    for i, v in ipairs(variants) do
        table.insert(dropdown_items, i .. ". " .. v)
    end

    table.insert(dialog, {class="label", label="Выберите вариант:", x=0, y=y_offset})
    table.insert(dialog, {class="dropdown", name="selected", items=dropdown_items, value=dropdown_items[1], x=0, y=y_offset + 1, width=4})
    y_offset = y_offset + 2

    table.insert(dialog, {class="label", label="Или отредактируйте:", x=0, y=y_offset})
    table.insert(dialog, {class="textbox", name="edited", value=variants[1], x=0, y=y_offset + 1, width=4, height=3})
    y_offset = y_offset + 4

    table.insert(dialog, {class="label", label="Feedback для retry:", x=0, y=y_offset, width=2})
    table.insert(dialog, {class="edit", name="feedback", value="", x=0, y=y_offset + 1, width=4})

    -- Кнопки с учётом картинки
    local buttons
    if image_path then
        buttons = {"Применить", "Показать картинку", "Retry", "Отмена"}
    else
        buttons = {"Применить", "Retry", "Отмена"}
    end

    local btn, res = aegisub.dialog.display(dialog, buttons)

    if btn == "Отмена" then
        return
    elseif btn == "Показать картинку" then
        open_image(image_path)
        -- Показываем диалог снова
        show_result_dialog(subs, active, line, response, request_data, source_blocks, num_variants)
        return
    elseif btn == "Retry" then
        request_data.feedback = res.feedback ~= "" and res.feedback or "дай другие варианты"
        write_json_file(request_file, request_data)

        -- Удаляем старый response перед retry
        delete_file(response_file)

        aegisub.progress.task("AI думает снова...")
        if run_python_backend() then
            local new_response = read_json_file(response_file)
            if new_response and new_response.variants then
                show_result_dialog(subs, active, line, new_response, request_data, source_blocks, num_variants)
            end
        end
        return
    else
        -- Применить
        local final = res.edited
        if final == variants[1] then
            local num = tonumber(res.selected:match("^(%d+)%."))
            if num and num > 1 and num <= #variants then
                final = variants[num]
            end
        end
        line.text = final
        subs[active] = line
        aegisub.set_undo_point("AI Subtitle Edit")
    end
end

-- ============== Batch перевод ==============

local function translate_batch(subs, sel, active)
    if #sel < 2 then
        aegisub.dialog.display({{class="label", label="Выделите 2+ строки для batch перевода!"}}, {"OK"})
        return
    end

    local settings = load_settings()

    -- Подтверждение
    local confirm = aegisub.dialog.display({
        {class="label", label="Batch перевод " .. #sel .. " строк", x=0, y=0},
        {class="label", label="Это может занять время.", x=0, y=1},
    }, {"Начать", "Отмена"})

    if confirm ~= "Начать" then return end

    -- Собираем строки
    local batch_lines = {}
    for _, i in ipairs(sel) do
        if subs[i].class == "dialogue" then
            table.insert(batch_lines, {
                idx = i,
                en = subs[i].text,
                ru = subs[i].text
            })
        end
    end

    local request_data = {
        batch_lines = batch_lines
    }

    write_json_file(request_file, request_data)

    aegisub.progress.task("AI обрабатывает " .. #batch_lines .. " строк...")
    local success = run_python_backend()

    if not success then
        aegisub.dialog.display({{class="label", label="Ошибка batch перевода!"}}, {"OK"})
        return
    end

    local response = read_json_file(response_file)
    if not response or not response.batch_variants then
        aegisub.dialog.display({{class="label", label="Нет результатов batch."}}, {"OK"})
        return
    end

    -- Применяем первый вариант к каждой строке
    local applied = 0
    for i, line_data in ipairs(batch_lines) do
        local variants = response.batch_variants[i]
        if variants and #variants > 0 then
            local line = subs[line_data.idx]
            line.text = variants[1]
            subs[line_data.idx] = line
            applied = applied + 1
        end
    end

    aegisub.set_undo_point("AI Batch Translate")
    aegisub.dialog.display({{class="label", label="Применено: " .. applied .. " из " .. #batch_lines}}, {"OK"})
end

-- ============== Регистрация ==============

aegisub.register_macro(
    script_name .. "/Перевести строку",
    "Перевести текущую строку с AI",
    translate_line
)

aegisub.register_macro(
    script_name .. "/Batch перевод (выделенные)",
    "Перевести несколько выделенных строк",
    translate_batch
)

aegisub.register_macro(
    script_name .. "/Настройки проекта",
    "Настроить глобальный контекст, стиль, инструкции",
    show_project_settings
)
