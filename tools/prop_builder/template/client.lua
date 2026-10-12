-- /spawnprop __NAME__        spawns the prop in front of you
-- /spawnprop __NAME__ delete removes it
local spawned = {}

RegisterCommand('spawnprop', function(_, args)
    if args[1] ~= '__NAME__' then return end
    local model = `__NAME__`

    if spawned.entity and DoesEntityExist(spawned.entity) then DeleteEntity(spawned.entity) end
    spawned.entity = nil
    if args[2] == 'delete' then return end

    if not IsModelInCdimage(model) then
        print('^1[__NAME__] model not found in the game files^7')
        return
    end
    RequestModel(model)
    local timeout = GetGameTimer() + 5000
    while not HasModelLoaded(model) and GetGameTimer() < timeout do Wait(0) end
    if not HasModelLoaded(model) then
        print('^1[__NAME__] model failed to load^7')
        return
    end

    local ped = PlayerPedId()
    local pos = GetOffsetFromEntityInWorldCoords(ped, 0.0, 1.5, 0.0)
    spawned.entity = CreateObject(model, pos.x, pos.y, pos.z, false, false, false)
    PlaceObjectOnGroundProperly(spawned.entity)
    SetEntityHeading(spawned.entity, GetEntityHeading(ped) + 180.0)
    SetModelAsNoLongerNeeded(model)
end, false)

AddEventHandler('onResourceStop', function(res)
    if res == GetCurrentResourceName() and spawned.entity and DoesEntityExist(spawned.entity) then
        DeleteEntity(spawned.entity)
    end
end)
