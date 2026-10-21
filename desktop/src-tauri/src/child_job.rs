//! Windows closes the entire local agent tree even if the desktop crashes.
#[cfg(windows)]
pub struct ChildJob(windows_sys::Win32::Foundation::HANDLE);
#[cfg(windows)]
unsafe impl Send for ChildJob {}
#[cfg(windows)]
impl ChildJob {
    pub fn attach(child: &std::process::Child) -> std::io::Result<Self> {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::{Foundation::CloseHandle, System::JobObjects::*};
        unsafe {
            let handle=CreateJobObjectW(std::ptr::null(),std::ptr::null());
            if handle.is_null() {return Err(std::io::Error::last_os_error());}
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION=std::mem::zeroed();
            limits.BasicLimitInformation.LimitFlags=JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(handle,JobObjectExtendedLimitInformation,&limits as *const _ as _,std::mem::size_of_val(&limits) as u32)==0
                || AssignProcessToJobObject(handle,child.as_raw_handle())==0 {
                let error=std::io::Error::last_os_error();CloseHandle(handle);return Err(error);
            }
            Ok(Self(handle))
        }
    }
}
#[cfg(windows)]
impl Drop for ChildJob { fn drop(&mut self) {unsafe {windows_sys::Win32::Foundation::CloseHandle(self.0);}} }

#[cfg(not(windows))]
pub struct ChildJob;
#[cfg(not(windows))]
impl ChildJob { pub fn attach(_: &std::process::Child)->std::io::Result<Self>{Ok(Self)} }

#[cfg(all(test,windows))]
mod tests {
    use super::*;
    #[test]
    fn closing_job_reaps_agent() {
        use std::os::windows::process::CommandExt;
        let mut child=std::process::Command::new("ping.exe").args(["-n","60","127.0.0.1"]).creation_flags(0x08000000).stdout(std::process::Stdio::null()).spawn().unwrap();
        let job=ChildJob::attach(&child).unwrap();
        assert!(child.try_wait().unwrap().is_none());
        drop(job);
        for _ in 0..100 {if child.try_wait().unwrap().is_some(){return;}std::thread::sleep(std::time::Duration::from_millis(20));}
        let _=child.kill();panic!("agent survived job close");
    }
}
