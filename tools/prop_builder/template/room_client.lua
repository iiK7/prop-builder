-- /spawnroom __NAME__          places the room in front of you, open side facing you
-- /spawnroom __NAME__ delete   removes it
local ROOM = {
    shell = `__NAME___shell`,
    depth = __DEPTH__,
    floor = __FLOOR__,
    objects = {
__OBJECTS__
    },
}
local spawned = {}

local function load(model)
    if not IsModelInCdimage(model) then return false end
    RequestModel(model)
    local timeout = GetGameTimer() + 5000
    while not HasModelLoaded(model) and GetGameTimer() < timeout do Wait(0) end
    return HasModelLoaded(model)
end

local function clear()
    for _, e in ipairs(spawned) do
        if DoesEntityExist(e) then DeleteEntity(e) end
    end
    spawned = {}
end

local function place(model, coords, heading)
    if not load(model) then
        print(('^1[__NAME__] model %s failed to load^7'):format(model))
        return nil
    end
    local e = CreateObject(model, coords.x, coords.y, coords.z, false, false, false)
    SetEntityHeading(e, heading)
    FreezeEntityPosition(e, true)
    SetModelAsNoLongerNeeded(model)
    spawned[#spawned + 1] = e
    return e
end

RegisterCommand('spawnroom', function(_, args)
    if args[1] ~= '__NAME__' then return end
    clear()
    if args[2] == 'delete' then return end

    local ped = PlayerPedId()
    local heading = GetEntityHeading(ped)
    local base = GetOffsetFromEntityInWorldCoords(ped, 0.0, ROOM.depth / 2 + 1.0, 0.0)
    local found, groundZ = GetGroundZFor_3dCoord(base.x, base.y, base.z + 1.0, false)
    if found then base = vector3(base.x, base.y, groundZ) end

    local shell = place(ROOM.shell, base, heading)
    if not shell then return end
    for _, o in ipairs(ROOM.objects) do
        place(o.model, GetOffsetFromEntityInWorldCoords(shell, o.x, o.y, ROOM.floor), heading)
    end
end, false)

AddEventHandler('onResourceStop', function(res)
    if res == GetCurrentResourceName() then clear() end
end)
