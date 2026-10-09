-- Headless driver for neovain. Invoked as: nvim --clean --headless -l driver.lua <job.json>
-- Applies steps to a buffer, stops at the first failure, writes the result to job.out.
local job = vim.json.decode(table.concat(vim.fn.readfile(arg[1]), "\n"))
local result = { ok = true, steps = {} }

local function finish()
  vim.fn.writefile({ vim.json.encode(result) }, job.report)
  vim.cmd("qall!")
end

-- job.file is a copy neovain made of the file asked for: a step that writes anyway writes the
-- copy, so neovain stays the only one that can touch the real file.
-- noautocmd skips filetype detection and ftplugins, which cost ~450ms and are disabled anyway.
vim.cmd("silent noautocmd edit " .. vim.fn.fnameescape(job.file))
-- Empty or new files have no line endings to detect; Neovim on Windows would pick CRLF.
if vim.fn.getfsize(job.file) <= 0 then vim.bo.fileformat = "unix" end
-- Neutralize everything that makes typed text differ from the literal keys.
vim.cmd("filetype off | syntax off")
for opt, val in pairs({ autoindent = false, smartindent = false, cindent = false, indentexpr = "",
  formatoptions = "", textwidth = 0, fixendofline = false }) do
  vim.bo[opt] = val
end
-- Indent style for > < and typed <Tab>: tabs if the file already indents with tabs, else spaces.
local uses_tabs = vim.fn.search("^\t", "nw") > 0
vim.bo.expandtab = not uses_tabs
vim.bo.shiftwidth = uses_tabs and 0 or job.sw
vim.bo.softtabstop = 0
vim.o.wrapscan = false -- searches never silently wrap around the file
vim.o.ignorecase = false
vim.o.magic = true

local function cursor() return vim.api.nvim_win_get_cursor(0)[1] end
local function pos() return table.concat(vim.api.nvim_win_get_cursor(0), ":") end

-- "@pat"   jump to the single line matching pat (error on 0 or >1 matches)
-- "@N@pat" jump to the Nth match
local function anchor(spec)
  local n, pat = spec:match("^@(%d+)@(.*)$")
  if not pat then n, pat = nil, spec:sub(2) end
  if pat == "" then error("empty anchor") end
  local hits = {}
  for i, line in ipairs(vim.api.nvim_buf_get_lines(0, 0, -1, false)) do
    if vim.fn.match(line, pat) >= 0 then hits[#hits + 1] = i end
  end
  if #hits == 0 then error("anchor matched 0 lines: " .. pat) end
  local idx = tonumber(n)
  if not idx then
    if #hits > 1 then
      error(("anchor matched %d lines (%s): make the pattern more specific or use @N@"):format(#hits,
        table.concat(vim.list_slice(hits, 1, 10), ",")))
    end
    idx = 1
  end
  if idx > #hits then error(("anchor asked for match %d of %d"):format(idx, #hits)) end
  local col = vim.fn.match(vim.fn.getline(hits[idx]), pat)
  vim.api.nvim_win_set_cursor(0, { hits[idx], col })
end

for i, step in ipairs(job.steps) do
  local changed_before, pos_before = vim.b.changedtick, pos()
  local ok, err = pcall(function()
    if step:sub(1, 1) == "@" then
      anchor(step)
    elseif step:sub(1, 1) == ":" then
      vim.cmd(step:sub(2))
    else
      local keys = vim.api.nvim_replace_termcodes(step, true, false, true)
      vim.api.nvim_cmd({ cmd = "normal", bang = true, args = { keys } }, {})
      if vim.fn.mode() ~= "n" then error("step left editor in mode " .. vim.fn.mode()) end
    end
  end)
  result.steps[i] = { step = step, line = cursor(), changed = vim.b.changedtick ~= changed_before,
    moved = pos() ~= pos_before }
  if not ok then
    result.ok = false
    result.failed = i
    result.error = tostring(err):gsub("^.-Vim%(%a+%):", ""):gsub("^.-driver%.lua:%d+: ", ""):gsub("^Vim:", "")
    return finish()
  end
end

vim.cmd("silent write! " .. vim.fn.fnameescape(job.out))
finish()
