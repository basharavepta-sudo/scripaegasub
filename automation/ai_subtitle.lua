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

local function show_project_settings()
    local settings = load_settings()

    local style_items = {"natural", "formal", "casual", "literal"}
    local style_labels = {
        natural = "Естественный (разговорный)",
        formal = "Формальный (официальный)",
        casual = "Неформальный (сленг)",
        literal = "Буквальный (близко к оригиналу)"
    }

    local dialog = {
        {class="label", label="=== Настройки проекта ===", x=0, y=0, width=3},

        {class="label", label="Глобальный контекст (описание фильма/сериала):", x=0, y=1, width=3},
        {class="textbox", name="global_context", value=settings.global_context or "", x=0, y=2, width=3, height=3},

        {class="label", label="Инструкции по умолчанию (применяются ко всем переводам):", x=0, y=5, width=3},
        {class="textbox", name="default_instructions", value=settings.default_instructions or "", x=0, y=6, width=3, height=2},

        {class="label", label="Стиль перевода:", x=0, y=8},
        {class="dropdown", name="style", items=style_items, value=settings.translation_style or "natural", x=1, y=8, width=2},

        {class="label", label="Кол-во вариантов (1-5):", x=0, y=9},
        {class="intedit", name="num_variants", value=settings.num_variants or 3, min=1, max=5, x=1, y=9},

        {class="label", label="Строк контекста (0-5):", x=0, y=10},
        {class="intedit", name="context_lines", value=settings.context_lines or 1, min=0, max=5, x=1, y=10},

        {class="checkbox", name="use_source", label="Использовать английский исходник (.txt/.srt)", value=settings.use_source or false, x=0, y=11, width=3},

        {class="label", label="", x=0, y=12},
        {class="label", label="Примеры контекста:", x=0, y=13, width=3},
        {class="label", label="  'Это комедия про студентов'", x=0, y=14, width=3},
        {class="label", label="  'Научная фантастика, формальный язык'", x=0, y=15, width=3},
    }

    local buttons = {"Сохранить", "Отмена"}
    local button, results = aegisub.dialog.display(dialog, buttons)

    if button == "Сохранить" then
        session_settings.global_context = results.global_context
        session_settings.default_instructions = results.default_instructions
        session_settings.translation_style = results.style
        session_settings.num_variants = results.num_variants
        session_settings.context_lines = results.context_lines
        session_settings.use_source = results.use_source
        save_settings()
        aegisub.log("Настройки сохранены!\n")
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

local function run_python_backend()
    local cmd = python_executable .. ' "' .. python_script .. '" "' .. request_file .. '" "' .. response_file .. '" "' .. config_file .. '"'
    if separator == "/" then
        cmd = cmd .. " 2>&1"
    end
    local exit_code = os.execute(cmd)
    if type(exit_code) == "boolean" then
        return exit_code
    elseif type(exit_code) == "number" then
        return exit_code == 0
    end
    return exit_code ~= nil
end

-- ============== Главная функция перевода ==============

local function translate_line(subs, sel, active)
    local settings = load_settings()
    local line = subs[active]

    if line.class ~= "dialogue" then
        aegisub.dialog.display({{class="label", label="Выберите строку диалога!"}}, {"OK"})
        return
    end

    -- Диалог быстрых настроек
    local quick_dialog = {
        {class="label", label="Текущая строка: " .. (line.text:sub(1, 50) .. (#line.text > 50 and "..." or "")), x=0, y=0, width=3},
        {class="label", label="", x=0, y=1},
        {class="label", label="Инструкции для этого перевода (опционально):", x=0, y=2, width=3},
        {class="textbox", name="instructions", value="", x=0, y=3, width=3, height=2},
        {class="label", label="", x=0, y=5},
        {class="label", label="Вариантов:", x=0, y=6},
        {class="intedit", name="num_variants", value=settings.num_variants, min=1, max=5, x=1, y=6},
    }

    local btn, res = aegisub.dialog.display(quick_dialog, {"Перевести", "Настройки проекта", "Отмена"})

    if btn == "Настройки проекта" then
        show_project_settings()
        return translate_line(subs, sel, active)  -- Повторить после настройки
    elseif btn == "Отмена" then
        return
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

    -- Обновляем num_variants в config
    local config = read_json_file(config_file) or {}
    config.num_variants = res.num_variants
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

    -- Показываем результаты
    show_result_dialog(subs, active, line, response.variants, request_data, source_blocks, res.num_variants)
end

-- ============== Диалог результатов ==============

function show_result_dialog(subs, active, line, variants, request_data, source_blocks, num_variants)
    local truncate = function(text, max)
        max = max or 60
        return #text > max and text:sub(1, max) .. "..." or text
    end

    local dialog = {
        {class="label", label="Оригинал: " .. truncate(line.text), x=0, y=0, width=3},
        {class="label", label="", x=0, y=1},
    }

    -- Варианты
    local dropdown_items = {}
    for i, v in ipairs(variants) do
        table.insert(dropdown_items, i .. ". " .. v)
    end

    table.insert(dialog, {class="label", label="Выберите вариант:", x=0, y=2})
    table.insert(dialog, {class="dropdown", name="selected", items=dropdown_items, value=dropdown_items[1], x=0, y=3, width=3})

    table.insert(dialog, {class="label", label="Или отредактируйте:", x=0, y=4})
    table.insert(dialog, {class="textbox", name="edited", value=variants[1], x=0, y=5, width=3, height=3})

    table.insert(dialog, {class="label", label="", x=0, y=8})
    table.insert(dialog, {class="label", label="Feedback для retry (опционально):", x=0, y=9, width=3})
    table.insert(dialog, {class="edit", name="feedback", value="", x=0, y=10, width=3})

    local buttons = {"Применить", "Retry", "Отмена"}
    local btn, res = aegisub.dialog.display(dialog, buttons)

    if btn == "Отмена" then
        return
    elseif btn == "Retry" then
        request_data.feedback = res.feedback ~= "" and res.feedback or "дай другие варианты"
        write_json_file(request_file, request_data)

        aegisub.progress.task("AI думает снова...")
        if run_python_backend() then
            local new_response = read_json_file(response_file)
            if new_response and new_response.variants then
                show_result_dialog(subs, active, line, new_response.variants, request_data, source_blocks, num_variants)
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
