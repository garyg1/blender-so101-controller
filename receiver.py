import bpy
import socket
from mathutils import Matrix

# =========================================================================
# 1. CLEANUP PREVIOUS RUNS (Closes old socket & stops old timer loops)
# =========================================================================
print("\n--- Initializing Dumb Matrix Camera Tracker ---")

if getattr(bpy.types.WindowManager, "openarm_timer_active", False):
    try:
        bpy.types.WindowManager.openarm_timer_active = False
        print("[CLEANUP] Stopped previous background timer loop.")
    except Exception as e:
        print(f"[CLEANUP] Note while stopping timer: {e}")

if hasattr(bpy.types.WindowManager, "openarm_socket"):
    try:
        old_sock = bpy.types.WindowManager.openarm_socket
        old_sock.close()
        print("[CLEANUP] Successfully closed previous UDP socket.")
    except Exception as e:
        print(f"[CLEANUP] Note while closing old socket: {e}")
    delattr(bpy.types.WindowManager, "openarm_socket")


# =========================================================================
# 2. SETUP THE UDP NETWORK SOCKET
# =========================================================================
UDP_IP = "127.0.0.1"
UDP_PORT = 5005

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) 
sock.bind((UDP_IP, UDP_PORT))
sock.setblocking(False) 

# Persist the socket and state flag in Blender window memory
bpy.types.WindowManager.openarm_socket = sock
bpy.types.WindowManager.openarm_timer_active = True
print(f"[SYSTEM] Blender listening for pre-computed 4x4 matrix stream on port {UDP_PORT}...")


# =========================================================================
# 3. DUMB COPY BACKGROUND MATRIX LOOP
# =========================================================================
def update_camera_position():
    if not getattr(bpy.types.WindowManager, "openarm_timer_active", False):
        print("[SYSTEM] Background tracking loop successfully terminated.")
        return None 
        
    active_sock = getattr(bpy.types.WindowManager, "openarm_socket", None)
    if not active_sock:
        return None 
        
    latest_msg = None
    
    # Drain the UDP buffer fully to completely eliminate network latency
    while True:
        try:
            data, addr = active_sock.recvfrom(1024)
            latest_msg = data.decode('utf-8')
        except BlockingIOError:
            break
        except Exception as e:
            print(f"[ERROR] Socket read error: {e}")
            break

    if latest_msg:
        try:
            # Parse the 16 pre-computed float elements over the wire
            mat_values = list(map(float, latest_msg.split(',')))
            
            if len(mat_values) == 16:
                # Target the active scene camera
                cam = bpy.context.scene.camera
                if cam:
                    # Map the raw incoming stream segments directly to a 4x4 coordinate structure
                    cam.matrix_world = Matrix([
                        mat_values[0:4],   
                        mat_values[4:8],   
                        mat_values[8:12],  
                        mat_values[12:16]  
                    ])
                    
                    # Force the viewports to draw immediately
                    for window in bpy.context.window_manager.windows:
                        for area in window.screen.areas:
                            if area.type == 'VIEW_3D':
                                area.tag_redraw()
                            
        except Exception as e:
            print(f"[ERROR] Receiver matrix parsing exception: {str(e)}")
        
    return 0.01 # Re-execute this loop block in exactly 10ms (100Hz polling rate)


# =========================================================================
# 4. REGISTER AND INITIATE LOOP EXECUTION
# =========================================================================
if hasattr(bpy.app.timers, "register"):
    bpy.app.timers.register(update_camera_position)
    print("[SYSTEM] Dumb Copy 4x4 Camera Engine online.")
