-- __PACK__: props and rooms from the prop site, in one resource
-- /spawnprop <name>            spawns a prop in front of you      (/spawnprop <name> delete removes it)
-- /spawnroom <name>            places a room, door facing you     (/spawnroom <name> delete removes it)
local PROPS = {
__PROPS__
}
local ROOMS = {
__ROOMS__
}
local spawned = {}

local function load(model)
    if not IsModelInCdimage(model) then return false end
    RequestModel(model)
    local timeout = GetGameTimer() + 5000
    while not HasModelLoaded(model) and GetGameTimer() < timeout do Wait(0) end
    return HasModelLoaded(model)
end

local function clear(name)
    for _, e in ipairs(spawned[name] or {}) do
        if DoesEntityExist(e) then DeleteEntity(e) end
    end
    spawned[name] = nil
end

local function place(name, model, coords, heading, frozen)
    if not load(model) then
        print(('^1[__PACK__] model %s of %s failed to load^7'):format(model, name))
        return nil
    end
    local e = CreateObject(model, coords.x, coords.y, coords.z, false, false, false)
    SetEntityHeading(e, heading)
    if frozen then FreezeEntityPosition(e, true) end
    SetModelAsNoLongerNeeded(model)
    spawned[name] = spawned[name] or {}
    table.insert(spawned[name], e)
    return e
end

local function names(list)
    local out = {}
    for k in pairs(list) do out[#out + 1] = k end
    table.sort(out)
    return table.concat(out, ', ')
end

RegisterCommand('spawnprop', function(_, args)
    local name = args[1]
    if not PROPS[name] then
        print(('^3[__PACK__] props: %s^7'):format(names(PROPS)))
        return
    end
    clear(name)
    if args[2] == 'delete' then return end

    local ped = PlayerPedId()
    local pos = GetOffsetFromEntityInWorldCoords(ped, 0.0, 1.5, 0.0)
    local e = place(name, PROPS[name], pos, GetEntityHeading(ped) + 180.0, false)
    if e then PlaceObjectOnGroundProperly(e) end
end, false)

RegisterCommand('spawnroom', function(_, args)
    local name, room = args[1], ROOMS[args[1]]
    if not room then
        print(('^3[__PACK__] rooms: %s^7'):format(names(ROOMS)))
        return
    end
    clear(name)
    if args[2] == 'delete' then return end

    local ped = PlayerPedId()
    local heading = GetEntityHeading(ped)
    local base = GetOffsetFromEntityInWorldCoords(ped, 0.0, room.depth / 2 + 1.0, 0.0)
    local found, groundZ = GetGroundZFor_3dCoord(base.x, base.y, base.z + 1.0, false)
    if found then base = vector3(base.x, base.y, groundZ) end

    local shell = place(name, room.shell, base, heading, true)
    if not shell then return end
    for _, o in ipairs(room.objects) do
        place(name, o.model, GetOffsetFromEntityInWorldCoords(shell, o.x, o.y, room.floor), heading, true)
    end
end, false)

AddEventHandler('onResourceStop', function(res)
    if res ~= GetCurrentResourceName() then return end
    for name in pairs(spawned) do clear(name) end
end)
