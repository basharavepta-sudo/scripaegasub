script_name = "AI Subtitle Assistant"
script_description = "AI-powered subtitle editing with ALMA/Ollama"
script_author = "AI Assistant"
script_version = "2.0"

-- Настройка путей для модулей
-- Получаем путь к текущему скрипту, чтобы найти соседние файлы
local script_path = debug.getinfo(1).source:match("@?(.*[\\/])")
if not script_path then
    -- Fallback если не удалось определить путь
    script_path = aegisub.decode_path("?user/automation/autoload/")
end

local separator = package.config:sub(1,1)
package.path = package.path .. ";" .. script_path .. "modules" .. separator .. "?.lua"

local json = require("json")

-- Путь к временным файлам
local temp_dir = os.getenv("TEMP") or os.getenv("TMP") or "/tmp"
local request_file = temp_dir .. separator .. "aegisub_ai_request.json"
local response_file = temp_dir .. separator .. "aegisub_ai_response.json"

-- Пути к скриптам и конфигам
local python_script = script_path .. "alma_backend.py"
local config_file = script_path .. "config.json"

-- Определение команды Python (python3 на Linux/Mac, python на Windows)
local function get_python_cmd()
    if separator == "/" then
        -- Unix-like система (Linux, Mac)
        -- Проверяем наличие python3
        local handle = io.popen("which python3 2>/dev/null")
        if handle then
            local result = handle:read("*a")
            handle:close()
            if result and result ~= "" then
                return "python3"
            end
        end
        -- Fallback на python
        return "python"
    else
        -- Windows
        return "python"
    end
end

local python_executable = get_python_cmd()

-- Парсинг SRT файла (для исходника)
function parse_srt_file(filepath)
    local file = io.open(filepath, "r")
    if not file then
        return nil, "Cannot open source file"
    end

    local content = file:read("*all")
    file:close()

    local blocks = {}
    local current_block = {}
    local block_num = nil

    for line in content:gmatch("[^\r\n]+") do
        -- Удаляем BOM если есть
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
            -- Таймкод, пропускаем
        elseif line ~= "" and block_num then
            if current_block.text and current_block.text ~= "" then
                current_block.text = current_block.text .. "\n"
            end
            current_block.text = (current_block.text or "") .. line
        end
    end

    -- Добавляем последний блок
    if block_num and current_block.text then
        blocks[block_num] = current_block.text
    end

    return blocks
end

-- Безопасная запись JSON в файл
local function write_json_file(filepath, data)
    local file, err = io.open(filepath, "w")
    if not file then
        return false, "Cannot write to file: " .. (err or "unknown error")
    end

    local success, encoded = pcall(json.encode, data)
    if not success then
        file:close()
        return false, "JSON encode error: " .. tostring(encoded)
    end

    file:write(encoded)
    file:close()
    return true
end

-- Безопасное чтение JSON из файла
local function read_json_file(filepath)
    local file = io.open(filepath, "r")
    if not file then
        return nil, "Cannot open file"
    end

    local content = file:read("*all")
    file:close()

    if not content or content == "" then
        return nil, "File is empty"
    end

    local success, decoded = pcall(json.decode, content)
    if not success then
        return nil, "JSON decode error: " .. tostring(decoded)
    end

    return decoded
end

-- Выполнение Python скрипта
local function run_python_backend()
    local cmd = python_executable .. ' "' .. python_script .. '" "' .. request_file .. '" "' .. response_file .. '" "' .. config_file .. '"'

    -- Добавляем перенаправление stderr для отладки
    if separator == "/" then
        cmd = cmd .. " 2>&1"
    end

    local exit_code = os.execute(cmd)

    -- os.execute возвращает разные значения в разных версиях Lua
    -- В Lua 5.1: возвращает код выхода напрямую
    -- В Lua 5.2+: возвращает true/nil, тип завершения, код
    if type(exit_code) == "boolean" then
        return exit_code
    elseif type(exit_code) == "number" then
        return exit_code == 0
    end

    return exit_code ~= nil
end

-- Главная функция
function show_ai_dialog(subs, sel, active)
    -- Получаем активную строку
    local line = subs[active]
    local line_index = active

    -- Проверяем что это диалоговая строка
    if line.class ~= "dialogue" then
        aegisub.dialog.display({{class="label", label="Ошибка: выберите строку диалога (не комментарий/стиль)."}}, {"OK"})
        return
    end

    -- Диалог настроек
    local config_dialog = {
        {class="label", label="Контекст (строк до/после):", x=0, y=0},
        {class="intedit", name="context", value=2, min=0, max=10, x=1, y=0},
        {class="checkbox", name="use_source", label="Использовать английский исходник (из папки скрипта)", value=false, x=0, y=1, width=2}
    }

    local button, results = aegisub.dialog.display(config_dialog, {"OK", "Отмена"})
    if button == "Отмена" then return end

    local context_size = results.context
    local use_source = results.use_source

    -- Парсим source файл если нужно
    local source_blocks = nil
    if use_source then
        local sub_path = aegisub.decode_path("?script")
        local sub_name = aegisub.file_name()

        if sub_name then
            sub_name = sub_name:match("(.+)%..+$") or sub_name
        else
            sub_name = "unknown"
        end

        -- Пробуем разные варианты исходного файла
        local source_extensions = {".txt", ".srt", "_en.txt", "_en.srt", "_eng.txt", "_eng.srt"}
        local source_file = nil

        for _, ext in ipairs(source_extensions) do
            local try_path = sub_path .. separator .. sub_name .. ext
            local test_file = io.open(try_path, "r")
            if test_file then
                test_file:close()
                source_file = try_path
                break
            end
        end

        if source_file then
            source_blocks = parse_srt_file(source_file)
            if not source_blocks then
                aegisub.log("Внимание: Не удалось прочитать исходный файл\n")
            end
        else
            aegisub.log("Внимание: Исходный файл не найден в папке скрипта\n")
        end
    end

    -- Собираем маппинг индексов строк Aegisub на номера диалогов
    local line_id_map = {}
    local dialogue_count = 0
    local scan_limit = math.min(#subs, line_index + context_size + 100)

    for i = 1, scan_limit do
        if subs[i].class == "dialogue" then
            dialogue_count = dialogue_count + 1
            line_id_map[i] = dialogue_count
        end
    end

    -- Проверяем что текущая строка есть в маппинге
    local current_d_idx = line_id_map[line_index]
    if not current_d_idx then
        aegisub.dialog.display({{class="label", label="Ошибка: не удалось определить номер строки."}}, {"OK"})
        return
    end

    -- Собираем контекст
    local context_before = {}
    local context_after = {}

    for i = math.max(1, line_index - context_size), line_index - 1 do
        local ctx_line = subs[i]
        if ctx_line.class == "dialogue" then
            local d_idx = line_id_map[i]
            table.insert(context_before, {
                ru = ctx_line.text,
                en = source_blocks and d_idx and source_blocks[d_idx] or ""
            })
        end
    end

    for i = line_index + 1, math.min(#subs, line_index + context_size) do
        local ctx_line = subs[i]
        if ctx_line.class == "dialogue" then
            local d_idx = line_id_map[i]
            table.insert(context_after, {
                ru = ctx_line.text,
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
        feedback = ""
    }

    -- Сохраняем JSON запрос
    local success, err = write_json_file(request_file, request_data)
    if not success then
        aegisub.dialog.display({{class="label", label="Ошибка записи запроса: " .. err}}, {"OK"})
        return
    end

    -- Запускаем Python
    aegisub.progress.task("AI думает...")
    local python_success = run_python_backend()

    if not python_success then
        aegisub.dialog.display({{class="label", label="Ошибка: Python скрипт завершился с ошибкой.\nПроверьте установку Python и зависимостей."}}, {"OK"})
        return
    end

    -- Читаем ответ
    local response, read_err = read_json_file(response_file)
    if not response then
        aegisub.dialog.display({{class="label", label="Ошибка чтения ответа: " .. (read_err or "неизвестная ошибка")}}, {"OK"})
        return
    end

    if response.error then
        aegisub.dialog.display({{class="label", label="Ошибка AI: " .. response.error}}, {"OK"})
        return
    end

    if not response.variants or #response.variants == 0 then
        aegisub.dialog.display({{class="label", label="Ошибка: AI не вернул вариантов."}}, {"OK"})
        return
    end

    -- Показываем результаты
    show_result_dialog(subs, active, line, response.variants, request_data, source_blocks)
end

-- Диалог выбора варианта
function show_result_dialog(subs, active, line, variants, request_data, source_blocks)
    -- Обрезаем длинный текст для label
    local function truncate(text, max_len)
        max_len = max_len or 80
        if #text > max_len then
            return text:sub(1, max_len) .. "..."
        end
        return text
    end

    local original_ru = line.text
    local original_en = request_data.current_line.en or ""

    local config_dialog = {
        {class="label", label="Оригинал (RU): " .. truncate(original_ru), x=0, y=0, width=3},
        {class="label", label="Оригинал (EN): " .. truncate(original_en), x=0, y=1, width=3},
        {class="label", label="", x=0, y=2},
        {class="label", label="Выберите вариант:", x=0, y=3},
    }

    -- Создаем dropdown с вариантами
    local dropdown_items = {}
    for i, variant in ipairs(variants) do
        table.insert(dropdown_items, tostring(i) .. ". " .. variant)
    end

    table.insert(config_dialog, {class="dropdown", name="selected_variant", items=dropdown_items, value=dropdown_items[1], x=0, y=4, width=3})
    table.insert(config_dialog, {class="label", label="", x=0, y=5})
    table.insert(config_dialog, {class="label", label="Или отредактируйте вручную:", x=0, y=6})
    table.insert(config_dialog, {class="textbox", name="edited", value=variants[1], x=0, y=7, width=3, height=4})

    local buttons = {"Применить", "Retry", "Отмена"}
    local button, results = aegisub.dialog.display(config_dialog, buttons)

    if button == "Отмена" then
        return
    elseif button == "Retry" then
        -- Запрашиваем фидбек
        local feedback_dialog = {
            {class="label", label="Что не нравится? (например: 'сделай короче', 'более разговорно')", x=0, y=0, width=3},
            {class="textbox", name="feedback", value="", width=3, height=2, x=0, y=1}
        }
        local fb_button, fb_results = aegisub.dialog.display(feedback_dialog, {"Отправить", "Отмена"})

        if fb_button == "Отправить" and fb_results.feedback ~= "" then
            request_data.feedback = fb_results.feedback

            -- Повторный запрос
            local success, err = write_json_file(request_file, request_data)
            if not success then
                aegisub.dialog.display({{class="label", label="Ошибка записи: " .. err}}, {"OK"})
                return
            end

            aegisub.progress.task("AI думает снова...")
            local python_success = run_python_backend()

            if not python_success then
                aegisub.dialog.display({{class="label", label="Ошибка при повторном запросе."}}, {"OK"})
                return
            end

            local new_response, read_err = read_json_file(response_file)
            if new_response and not new_response.error and new_response.variants then
                show_result_dialog(subs, active, line, new_response.variants, request_data, source_blocks)
            else
                aegisub.dialog.display({{class="label", label="Ошибка при повторе: " .. (new_response and new_response.error or read_err or "неизвестная ошибка")}}, {"OK"})
            end
        end
        return
    else
        -- Применяем вариант
        local final_text = results.edited

        -- Если текст не был отредактирован (равен первому варианту),
        -- но выбран другой в dropdown - используем выбранный
        if final_text == variants[1] then
            -- Парсим номер выбранного варианта из dropdown
            local selected_num = tonumber(results.selected_variant:match("^(%d+)%."))
            if selected_num and selected_num > 1 and selected_num <= #variants then
                final_text = variants[selected_num]
            end
        end

        line.text = final_text
        subs[active] = line
        aegisub.set_undo_point("AI Subtitle Edit")
    end
end

-- Регистрация макроса
aegisub.register_macro(script_name, script_description, show_ai_dialog)
