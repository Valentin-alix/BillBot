import idaapi
import idautils


def get_operation_index_inside_function(ea: int) -> int:
    func = idaapi.get_func(ea)
    if not func:
        err = f"func at {ea} not found"
        raise ValueError(err)

    for index, item_ea in enumerate(idautils.FuncItems(func.start_ea)):
        if item_ea == ea:
            return index

    err = f"Did not found operation at {ea} ?!"
    raise ValueError(err)
