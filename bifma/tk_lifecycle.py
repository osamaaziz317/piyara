"""Widget-owned Tcl variables shared by the native and historical workspaces."""

from threading import get_ident
import tkinter as tk


def owned_variable(owner, *, boolean=False, value=None):
    """Tie a Tcl variable to its widget, even when Python cycles outlive it.

    Tkinter's Variable finalizer can run during a worker's cyclic collection.
    Dispose Tcl state on the owner's Destroy event and detach interpreter/root
    references so delayed collection cannot make a cross-thread Tcl call.
    """
    if not hasattr(owner, "_bifma_variables"):
        owner._bifma_variables = []
        owner_thread = get_ident()
        def release(event):
            if event.widget is not owner:
                return
            if get_ident() != owner_thread:
                raise RuntimeError("Interface variables must be released by their owning thread.")
            variables, owner._bifma_variables = owner._bifma_variables, []
            errors = []
            for variable in variables:
                interpreter = variable._tk
                if interpreter is None:
                    continue
                try:
                    # Remove traces before unset: callbacks must not act on
                    # widgets whose destruction has already begun.
                    for mode, command in variable.trace_info():
                        variable.trace_remove(mode, command)
                    if interpreter.getboolean(interpreter.call("info", "exists", str(variable))):
                        interpreter.globalunsetvar(str(variable))
                    for command in tuple(variable._tclCommands or ()):
                        interpreter.deletecommand(command)
                except Exception as error:
                    errors.append(error)
                finally:
                    # Both supported CPython Tkinter versions use this guard
                    # in Variable.__del__. Release references on the Tk thread.
                    variable._tclCommands = None
                    variable._tk = None
                    variable._root = None
            if errors:
                raise errors[0]
        owner.bind("<Destroy>", release, add=True)
    variable = (tk.BooleanVar if boolean else tk.StringVar)(master=owner, value=value)
    owner._bifma_variables.append(variable)
    return variable
