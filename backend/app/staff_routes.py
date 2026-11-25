from uuid import UUID
from fastapi import Depends, Header
from .contracts import SessionView
from .staff_contracts import StaffApplication, WrittenPreview, ExecuteWritten, SyntheticContact, SupportCatalog
from .staff import StaffService


def mount_staff(app,coordinator,staff_owner,key):
    staff=StaffService(coordinator)

    @app.get('/api/v1/staff/sessions/{sid}',response_model=SessionView)
    def session(sid:UUID,user=Depends(staff_owner)):
        from .coordinator import owned,snapshot
        with coordinator.repo.read() as db:
            return snapshot(db,owned(db,str(sid),user),staff=True)

    @app.get('/api/v1/staff/applications',response_model=list[StaffApplication])
    def applications(user=Depends(staff_owner)):
        return staff.applications(user)

    @app.get('/api/v1/staff/applications/{aid}',response_model=StaffApplication)
    def detail(aid:UUID,user=Depends(staff_owner)):
        return staff.detail(user,str(aid))

    @app.get('/api/v1/staff/support-catalog',response_model=SupportCatalog)
    def catalog(user=Depends(staff_owner)):
        return staff.catalog(user)

    @app.get('/api/v1/staff/sessions/{sid}/written-preview',response_model=WrittenPreview)
    def preview(sid:UUID,user=Depends(staff_owner)):
        return staff.preview(user,str(sid))

    @app.post('/api/v1/staff/sessions/{sid}/reject',response_model=SessionView,status_code=202)
    def reject(sid:UUID,body:ExecuteWritten,user=Depends(staff_owner),idempotency_key:str|None=Header(default=None)):
        return staff.reject(user,str(sid),body,key(idempotency_key))

    @app.post('/api/v1/staff/sessions/{sid}/contact-events',response_model=SessionView)
    def contact(sid:UUID,body:SyntheticContact,user=Depends(staff_owner)):
        return staff.contact(user,str(sid),body)
