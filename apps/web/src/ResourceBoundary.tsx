import { Component, type ReactNode } from 'react';

export class ResourceBoundary extends Component<{children:ReactNode; title?:string; fullPage?:boolean},{failed:boolean}> {
  state = {failed:false};
  static getDerivedStateFromError() { return {failed:true}; }
  render() {
    if (!this.state.failed) return this.props.children;
    return <div className={`resource-error${this.props.fullPage ? ' full-page' : ''}`} role="alert">
      <div className="resource-error-card"><span className="eyebrow">CodeScape</span>
        <h2>{this.props.title || '页面暂时无法显示'}</h2>
        <p>软件更新或资源加载中断。已保存的项目数据仍在本机，请刷新页面继续。</p>
        <button className="primary-button" onClick={()=>window.location.reload()}>刷新页面</button>
      </div>
    </div>;
  }
}
